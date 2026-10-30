"""Staging sidecar entry point; no developer workstation paths or auth bypasses."""
import hashlib
import json
import os
from pathlib import Path

from .store import Conflict

SCHOOL_RUNTIME_SETTINGS = ('PROCESS_GEMINI_MODEL', 'GEMINI_API_BASE',
                          'IMPLEMENTATION_PRICE_OCR_DEVICE', 'IMPLEMENTATION_PRICE_OCR_DPI')


def source_manifest(root):
    root = Path(root).resolve()
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def verify_source(root, manifest):
    if any(p.is_symlink() for p in Path(root).rglob("*")):
        raise Conflict("固定業務ソースにシンボリックリンクは使用できません")
    expected = json.loads(Path(manifest).read_text())
    if not expected.get('source_revision') or source_manifest(root) != expected.get('files'):
        raise Conflict('業務APIの固定ソースを照合できません')


def configuration(root):
    root = Path(root)
    bundle = json.loads((root / 'release.json').read_text())
    if set(bundle['domains']) != {'hospital','shift','school-lunch'}:
        raise RuntimeError('3業務のリリース情報が必要です')
    required = ('SAWA_TRIAL_WEB_TOKEN','SAWA_TRIAL_OPERATOR_ACCOUNT','PORTAL_AUTH_ME_URL')
    if any(not os.environ.get(key) for key in required):
        raise RuntimeError('SAWA認証・試用担当者の設定が不足しています')
    if len(os.environ['SAWA_TRIAL_WEB_TOKEN']) < 32:
        raise RuntimeError('試用接続トークンが短すぎます')
    domains = {}
    for domain, release in bundle['domains'].items():
        source = root / 'native' / domain
        manifest = root / 'manifests' / (domain+'.json')
        verify_source(source, manifest)
        baseline = root / 'baselines' / domain
        actual = source_manifest(baseline)
        if actual != release['baseline_files']:
            raise RuntimeError('試用baselineの内容を照合できません: '+domain)
        domains[domain] = {'worktree':str(source),'source_manifest':str(manifest),
            'baseline':str(baseline),'model_cache':'/opt/hf','python':'/opt/agent/'+domain+'/bin/python','env':{}}
        if domain == 'hospital':
            domains[domain]['facility_master_file'] = 'facility-master.json'
            domains[domain]['initialize_pending_sources'] = True
            for key in ('GEMINI_API_KEY','GOOGLE_API_KEY','GOOGLE_OAUTH_CLIENT_ID','OCR_PIPELINE_BUCKET','OCR_PIPELINE_URL','OCR_PIPELINE_TARGET_AUDIENCE'):
                if os.environ.get(key): domains[domain]['env'][key]=os.environ[key]
            domains[domain]['env']['WORKFLOW_V2_LLM_MODEL']=os.environ['GEMINI_AGENT_MODEL']
        elif domain == 'school-lunch':
            domains[domain]['registered_source'] = True
            if not os.environ.get('PROCESS_GEMINI_MODEL', '').strip():
                raise RuntimeError('学校給食の工程推測モデル設定が不足しています')
            for key in ('GEMINI_API_KEY','GOOGLE_API_KEY') + SCHOOL_RUNTIME_SETTINGS:
                if os.environ.get(key): domains[domain]['env'][key]=os.environ[key]
        if domain != 'hospital':
            domains[domain]['env'].update(AUTH_PROVIDER='portal',PORTAL_AUTH_ME_URL=os.environ['PORTAL_AUTH_ME_URL'])
    return {'initial_provider':'gemini','run_cap_cents':300,'durability':'cloud-sql','portal_auth_url':os.environ['PORTAL_AUTH_ME_URL'],'domains':domains}


def main():
    import uvicorn
    from .runtime_gateway import create_gateway

    def initialize():
        from .app import create_app
        return create_app(configuration('/opt/sawa-agent'), Path('/var/lib/sawa-agent'))

    # This is a private sidecar reached only through the authenticated Next proxy.
    app = create_gateway(initialize)
    uvicorn.run(app,host='0.0.0.0',port=8128,workers=1)


if __name__ == '__main__':
    main()
