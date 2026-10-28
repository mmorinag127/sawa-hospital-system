"""Merge an explicit upstream delta into a pinned bundle without discarding extensions.

Conflicts are written visibly and block sealing/runtime verification. --seal is a
separate operation after reviewing all merged source and resolving conflicts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from .runtime import source_manifest


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def blob(repo, revision, path):
    if not git(repo,'ls-tree','-z',revision,'--',path):
        return None
    return git(repo,'show',revision+':'+path)


def published(path, manifest):
    return path in manifest['files'] or path.startswith(('backend/src/','backend/shift_scheduler_backend/',
                                                       'backend/migrations/','worker/service/','ocr_pipeline/app/'))


def working_snapshot(repo, manifest, target):
    if git(repo, 'rev-parse', 'HEAD^{commit}').decode().strip() != target:
        raise RuntimeError('Working source HEAD does not match the pinned target')
    names = git(repo, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')
    snapshot = {}
    for name in sorted(set(names)):
        if not name or not published(name, manifest):
            continue
        path = repo / name
        if path.is_symlink():
            raise RuntimeError('Working native source cannot contain symlinks: ' + name)
        if path.is_file():
            snapshot[name] = path.read_bytes()
    return snapshot


def snapshot_hashes(snapshot):
    return {name: hashlib.sha256(value).hexdigest() for name, value in snapshot.items()}


def merge(repo, bundle, manifest, target, report, *, working_tree=False):
    base = manifest['source_revision']
    target = git(repo,'rev-parse',target+'^{commit}').decode().strip()
    delta = git(repo,'diff','--binary',base,target,'--','backend','worker','ocr_pipeline','pyproject.toml')
    changed = git(repo,'diff','--name-only',base,target,'--','backend','worker','ocr_pipeline','pyproject.toml').decode().splitlines()
    snapshot = working_snapshot(repo, manifest, target) if working_tree else None
    if snapshot is not None:
        dirty = git(repo, 'diff', '--name-only', 'HEAD', '--', 'backend', 'worker', 'ocr_pipeline', 'pyproject.toml').decode().splitlines()
        changed = sorted(set(changed + dirty + [name for name in snapshot if blob(repo, target, name) is None]))
    result = {'base':base,'target':target,'delta_sha256':hashlib.sha256(delta).hexdigest(),
              'files':[],'conflicts':[]}
    if snapshot is not None:
        result['working_source_files'] = snapshot_hashes(snapshot)
    for relative in changed:
        if not published(relative,manifest):
            continue
        path=bundle/relative
        before = blob(repo, base, relative)
        after = snapshot.get(relative) if snapshot is not None else blob(repo,target,relative)
        current=path.read_bytes() if path.is_file() else None
        if current==after:
            continue
        if current==before:
            merged=after
            status=0
        elif before is None or after is None or current is None:
            result['conflicts'].append(relative)
            result['files'].append({'path':relative,'action':'manual_add_delete_conflict'})
            continue
        else:
            with tempfile.TemporaryDirectory(dir=report.parent) as folder:
                current_file,base_file,target_file=[Path(folder)/name for name in ('current','base','upstream')]
                for file,content in ((current_file,current),(base_file,before),(target_file,after)):
                    file.write_bytes(content)
                command=subprocess.run(['git','merge-file','-p','-L','BUNDLED_EXTENSION','-L',base,
                                        '-L',target,str(current_file),str(base_file),str(target_file)],capture_output=True)
                status=command.returncode
                if not 0 <= status <= 127:
                    raise RuntimeError('Cannot merge native source: '+relative)
                merged=command.stdout
        if merged is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(merged)
        if status:
            result['conflicts'].append(relative)
        result['files'].append({'path':relative,'action':'three_way' if current!=before else 'upstream_delta',
                                'conflict':bool(status)})
    if snapshot is not None and snapshot_hashes(working_snapshot(repo, manifest, target)) != result['working_source_files']:
        raise RuntimeError('Working native source changed during synchronization; do not seal')
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


def seal(repo,bundle,manifest_path,report):
    result=json.loads(report.read_text())
    if any(item['action']=='manual_add_delete_conflict' for item in result['files']):
        raise RuntimeError('An add/delete conflict requires an explicit source integration before sealing')
    target=result['target']
    manifest=json.loads(manifest_path.read_text())
    if 'working_source_files' in result:
        snapshot = working_snapshot(repo, manifest, target)
        if snapshot_hashes(snapshot) != result['working_source_files']:
            raise RuntimeError('Working native source changed after synchronization')
    extensions=[]
    actual=source_manifest(bundle)
    for relative,digest in actual.items():
        current=(bundle/relative).read_bytes()
        if b'<<<<<<< BUNDLED_EXTENSION' in current or b'>>>>>>> '+target.encode() in current:
            raise RuntimeError('Unresolved source conflict: '+relative)
        upstream=blob(repo,target,relative)
        upstream_digest=hashlib.sha256(upstream).hexdigest() if upstream is not None else None
        if upstream_digest!=digest:
            extensions.append({'path':relative,'upstream_sha256':upstream_digest,'bundled_sha256':digest})
    manifest.update(source_revision=target,upstream_base_revision=result['base'],
                    upstream_delta_sha256=result['delta_sha256'],files=actual,
                    retained_extensions=extensions,
                    working_diff_basis='canonical retained_extensions JSON',
                    working_diff_sha256=hashlib.sha256(json.dumps(extensions,sort_keys=True,separators=(',',':')).encode()).hexdigest())
    if 'working_source_files' in result:
        manifest['working_source_files'] = result['working_source_files']
    else:
        manifest.pop('working_source_files', None)
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--target',required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--seal',action='store_true')
    parser.add_argument('--working-tree', action='store_true', help='Include exact uncommitted source; record and recheck every published file hash')
    args=parser.parse_args()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    if args.seal:
        if git(args.repo,'rev-parse',args.target+'^{commit}').decode().strip() != json.loads(args.report.read_text())['target']:
            raise RuntimeError('The reviewed merge target does not match the requested target')
        seal(args.repo,args.bundle,args.manifest,args.report)
        return
    result=merge(args.repo,args.bundle,json.loads(args.manifest.read_text()),args.target,args.report,
                 working_tree=args.working_tree)
    print(json.dumps({'files':len(result['files']),'conflicts':result['conflicts']},ensure_ascii=False))
    if result['conflicts']:
        raise SystemExit(2)


if __name__=='__main__':
    main()
