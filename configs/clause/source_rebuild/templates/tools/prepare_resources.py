#!/usr/bin/env python3
"""Download the one missing local encoder; lexical resources ship in the pack."""
import argparse,json,hashlib
from pathlib import Path
from common import ROOT,dump
MODEL='FacebookAI/roberta-large'
REVISION='722cf37b1afa9454edce342e7895e588b6ff1d59'
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--download-bert',action='store_true');args=p.parse_args()
    if not args.download_bert:
        print('NLTK English Punkt and WordNet are bundled. To download BERTScore encoder: --download-bert');return
    from huggingface_hub import snapshot_download
    target=ROOT/'resources/models/roberta-large'
    snapshot_download(repo_id=MODEL,revision=REVISION,local_dir=str(target),allow_patterns=['config.json','tokenizer_config.json','tokenizer.json','vocab.json','merges.txt','model.safetensors'],local_dir_use_symlinks=False)
    required=['config.json','vocab.json','merges.txt','model.safetensors']
    assert all((target/p).is_file() for p in required),'Incomplete encoder download'
    files=[]
    for path in sorted(target.glob('*')):
        if path.is_file():
            h=hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
            files.append(dict(path=path.name,size=path.stat().st_size,sha256=h.hexdigest()))
    dump(target/'MODEL_REVISION.json',dict(repo_id=MODEL,revision=REVISION,files=files))
    print('Encoder ready:',target)
if __name__=='__main__':main()
