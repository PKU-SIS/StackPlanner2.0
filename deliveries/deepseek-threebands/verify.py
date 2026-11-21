"""Offline integrity check; no model, network, install or generation calls."""
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
for band in ('10000','20000','30000'):
 profile=ROOT/'profiles'/band
 expected=read(profile/'RUNTIME_HASHES.json')
 for relative,digest in expected.items():assert sha(profile/'code'/relative)==digest,(band,relative)
 for relative,digest in read(profile/'ORIGINAL_MANIFEST.json')['helper_hashes'].items():assert sha(profile/'runner'/relative)==digest
for variant in (ROOT/'historical-runtime-variants').iterdir():
 config=read(variant/'VARIANT.json');base=ROOT/'profiles'/config['base_profile']/'code'
 for relative,digest in config['runtime_hashes'].items():
  path=variant/'overrides'/relative if relative in config['copy_overrides'] else base/relative
  assert sha(path)==digest,(variant.name,relative)
if (ROOT/'SHA256SUMS.json').exists():
 for relative,digest in read(ROOT/'SHA256SUMS.json').items():assert sha(ROOT/relative)==digest,relative
print('All three profiles, historical runtime variants and file checksums verified.')
