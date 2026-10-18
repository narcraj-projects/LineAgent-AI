"""Isolated Nomic hosted evaluation. Never modifies app environment files or PDFs."""
from pathlib import Path
import json
import os
import subprocess
import sys
from time import sleep
from typing import Any
from datetime import datetime, timezone

from evals.run_suite import write_json
from src.adapters.evaluation import HTTPObservations
from src.adapters.fireworks_embeddings import FireworksEmbeddingAdapter
from src.adapters.langchain_documents import LangChainPDFLoader
from src.adapters.langchain_retrieval import ingest_with_langchain
from src.adapters.pinecone_documents import PineconeDocumentAdapter
from src.adapters.search_settings import load_search_settings
from src.retrieval.corpus import build_corpus, load_dataset
from src.retrieval.pdf_metadata import build_pdf_metadata, langchain_pdf_namespace_tag

ROOT = Path(__file__).resolve().parents[1]
INDEX = 'linemedic-nomic-benchmark-v1'
MODEL = 'nomic-ai/nomic-embed-text-v1.5'


def main() -> int:
    os.environ['PINECONE_INDEX_NAME'] = INDEX
    os.environ['FIREWORKS_EMBEDDING_MODEL'] = MODEL
    output = ROOT/'evals/results'/datetime.now(timezone.utc).strftime('nomic-hosted-%Y%m%dT%H%M%SZ')
    output.mkdir(parents=True, exist_ok=False)
    print(f'Hosted evaluation receipts: {output}',flush=True)
    loader = LangChainPDFLoader(ROOT/'output/pdf/manifest.json', load_dataset(ROOT/'data'))
    langchain_documents = loader.load()
    parsed = loader.parsed
    assert parsed is not None
    corpus = build_corpus(parsed.dataset)
    metadata = build_pdf_metadata(parsed)
    settings = load_search_settings(ROOT/'.env')
    observations = HTTPObservations()
    embedder = FireworksEmbeddingAdapter(settings.fireworks_api_key.get_secret_value(), MODEL, 768,
        settings.ca_file,client=observations.client(settings.ca_file))
    index = None
    receipt = {'synthetic':True,'model':MODEL,'dimension':768,'index':INDEX,'status':'NOT_RUN',
        'app_defaults_changed':False,'pdf_guides_changed':False,'cost_usd':None,
        'method':'Local runner against hosted providers, not a Railway candidate deployment'}
    try:
        def probe(dimension: int) -> None:
            if dimension != 768:
                raise ValueError('Unexpected candidate dimension')
            embedder.embed(('Synthetic evaluation index compatibility probe.',),'query')
        index = PineconeDocumentAdapter.connect(settings,corpus,create_dimension=768,before_create=probe,
            namespace_tag=langchain_pdf_namespace_tag(parsed.manifest),source_metadata=metadata)
        if index.configuration.dimension != 768:
            raise ValueError('Candidate index dimension mismatch')
        receipt.update({'namespace':index.namespace,'configuration':index.configuration.model_dump(mode='json')})
        write_json(output/'ingestion.json',receipt)
        count = ingest_with_langchain(embedder,index,corpus,metadata,index.upsert,langchain_documents)
        receipt['acknowledged_documents'] = count
        for attempt in range(6):
            try:
                index.verify_visible()
                receipt['visible_documents']=len(corpus)
                break
            except Exception:
                if attempt == 5:
                    raise
                sleep(1)
        receipt.update({'status':'PASS','observed_model':embedder.observed_model,
            'provider_responses':observations.records})
    except Exception:
        receipt.update({'status':'FAIL','reason':'Isolated index provisioning, ingestion or visibility unavailable; provider details withheld. Partial candidate resources may exist.',
            'provider_responses':observations.records})
    finally:
        write_json(output/'ingestion.json',receipt)
        embedder.close()
        if index:
            index.close()
    print(json.dumps({k:receipt[k] for k in ('status','index','model','dimension')}),flush=True)
    if receipt['status'] != 'PASS':
        print('Candidate evaluation NOT_RUN because hosted preparation failed.',flush=True)
        return 2
    # Keep shared latest pointers pointing to their original baseline run.
    pointers = [ROOT/'evals/results/latest-frozen30.txt',ROOT/'evals/results/latest-pdf-recall.txt']
    original = {p:p.read_bytes() if p.exists() else None for p in pointers}
    try:
        with (output/'evaluation-console.txt').open('w',encoding='utf-8') as log:
            result = subprocess.run([sys.executable,'-u','-m','evals.run_suite','--graph-backend','neo4j',
                '--pdf-manifest',str(ROOT/'output/pdf/manifest.json')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
        candidate = pointers[0].read_text().strip()
        candidate_manifest = json.loads((ROOT/'evals/results'/candidate/'manifest.json').read_text())
        if (candidate_manifest.get('completed_cells') != 120
                or candidate_manifest['setup']['retrieval']['embedding_model'] != MODEL
                or candidate_manifest['setup']['retrieval']['configuration']['index_name'] != INDEX):
            raise ValueError('Candidate suite incomplete or identity mismatched; do not reuse a previous pointer')
        receipt['evaluation_run']=candidate
        receipt['evaluation_exit_code']=result.returncode
        print('Frozen evaluation complete:',candidate,'exit',result.returncode,flush=True)
        with (output/'recall-console.txt').open('w',encoding='utf-8') as log:
            recall = subprocess.run([sys.executable,'-u','-m','scripts.check_pdf_retrieval'],cwd=ROOT,
                stdout=log,stderr=subprocess.STDOUT)
        receipt['recall_run']=pointers[1].read_text().strip()
        receipt['recall_exit_code']=recall.returncode
        write_json(output/'run-receipt.json',receipt)
        (ROOT/'evals/results/latest-nomic-hosted.txt').write_text(output.name+'\n',encoding='utf-8')
    finally:
        for path,value in original.items():
            if value is not None:
                path.write_bytes(value)
    print('Hosted provider evaluation complete. Deployed defaults and PDFs unchanged.',flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
