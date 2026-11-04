import contextlib
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import profile_stg_startup as probe

def service(name='worker-stg'):
    return {'apiVersion':'serving.knative.dev/v1','kind':'Service',
        'metadata':{'name':name,'resourceVersion':'1'},
        'spec':{'template':{'metadata':{'name':name+'-00001-abc','annotations':{'run.googleapis.com/startup-cpu-boost':'false'}},
                'spec':{'containers':[{'image':'asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend@sha256:'+'a'*64,
                    'env':[{'name':'OPERATOR_PASSWORD','value':'must-not-print'}],
                    'resources':{'limits':{'cpu':'2','memory':'2Gi'}}}], 'serviceAccountName':'existing-runtime'}},
            'traffic':[{'latestRevision':True,'percent':100}]},
        'status':{'latestReadyRevisionName':name+'-00001-abc','traffic':[{'revisionName':name+'-00001-abc','percent':100}]}}

class StartupProfileTests(unittest.TestCase):
    def test_only_staging_targets(self):
        with self.assertRaises(ValueError), patch.object(probe,'cli') as cli:
            probe.describe('worker-prod')
        cli.assert_not_called()

    def test_immutable_only(self):
        original=service()
        original['spec']['template']['spec']['containers'][0]['image']='repo:latest'
        with self.assertRaises(ValueError): probe.image_and_revision(original)

    def test_existing_tag_is_pinned_to_the_serving_revision_digest(self):
        original=service()
        original['spec']['template']['spec']['containers'][0]['image']='repo:existing-tag'
        digest='asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend@sha256:'+'b'*64
        original['status']['_profile_image_digest']=digest
        candidate=probe.probe_document(original,'worker-stg','123-1')
        self.assertEqual(candidate['spec']['template']['spec']['containers'][0]['image'],digest)
        self.assertEqual(original['spec']['template']['spec']['containers'][0]['image'],'repo:existing-tag')

    def test_no_existing_command_override(self):
        original=service()
        original['spec']['template']['spec']['containers'][0]['args']=['custom']
        with self.assertRaises(ValueError): probe.image_and_revision(original)

    def test_no_split_or_tagged_traffic(self):
        for traffic in ([{'revisionName':'','percent':100}],
                        [{'revisionName':'worker-stg-00001-abc','percent':50}],
                        [{'revisionName':'worker-stg-00001-abc','percent':100,'tag':'user'}]):
            original=service();original['status']['traffic']=traffic
            with self.assertRaises(ValueError): probe.image_and_revision(original)

    def test_serving_revision_not_unused_latest_ready_is_authoritative(self):
        original=service()
        original['status']['latestReadyRevisionName']='worker-stg-unused-diagnostic'
        self.assertEqual(probe.image_and_revision(original)[1],'worker-stg-00001-abc')

    def test_preserves_image_env_identity_resources_and_original(self):
        for name in probe.TARGETS:
            original=service(name);before=deepcopy(original)
            doc=probe.probe_document(original,name,'123-1')
            self.assertEqual(original,before)
            self.assertNotIn('status',doc)
            self.assertEqual(doc['spec']['traffic'],[{'revisionName':name+'-00001-abc','percent':100},
                {'revisionName':name+'-profile-123-1','percent':0,'tag':'startup-profile'}])
            container=doc['spec']['template']['spec']['containers'][0]
            previous=original['spec']['template']['spec']['containers'][0]
            for key in ('image','env','resources'): self.assertEqual(container[key],previous[key])
            self.assertIn('python -X importtime -m uvicorn',container['args'][1])
            self.assertEqual(doc['spec']['template']['spec']['serviceAccountName'],'existing-runtime')
            self.assertEqual(doc['spec']['template']['metadata']['annotations'],before['spec']['template']['metadata']['annotations'])

    def test_restore_retains_original_traffic_while_selecting_existing_revision(self):
        original=service();candidate=probe.probe_document(original,'worker-stg','123-1')
        current=deepcopy(candidate);current['status']={'latestReadyRevisionName':'worker-stg-profile-123-1'}
        restored=probe.restored_document(original,current,candidate)
        self.assertEqual(restored['spec']['template'],original['spec']['template'])
        self.assertEqual(restored['spec']['traffic'],[{'revisionName':'worker-stg-00001-abc','percent':100}])

    def test_omitted_template_name_is_not_added_during_restore(self):
        original=service(); original['spec']['template']['metadata'].pop('name')
        candidate=probe.probe_document(original,'worker-stg','123-1')
        restored=probe.restored_document(original,candidate,candidate)
        self.assertNotIn('name',restored['spec']['template']['metadata'])

    def test_latest_policy_accepts_only_equivalent_normal_launch(self):
        original=service();original['spec']['template']['metadata'].pop('name')
        original['status']['_profile_active_launch']={'command':None,'args':None}
        final=deepcopy(original)
        final['status'].update(latestReadyRevisionName='worker-stg-restored-normal',traffic=[{'revisionName':'worker-stg-restored-normal','percent':100}])
        self.assertTrue(probe.restoration_matches(original,final))
        final['status']['_profile_active_launch']={'command':['/bin/sh'],'args':['profile']}
        self.assertFalse(probe.restoration_matches(original,final))

    def test_reviewed_extra_name_recovery_requires_both_full_spec_hashes(self):
        original=service();original['spec']['template']['metadata'].pop('name')
        current=deepcopy(original);current['spec']['template']['metadata']['name']='worker-stg-00001-abc'
        expected={'image':probe.image_and_revision(original)[0],'revision':'worker-stg-00001-abc',
                  'spec_sha256':probe.spec_hash(original),'recover_from_sha256':probe.spec_hash(current)}
        with patch.object(probe,'replace') as replace,patch.object(probe,'describe',return_value=original):
            self.assertEqual(probe.recover_reviewed_name(current,expected,'unused'),original)
            self.assertNotIn('name',replace.call_args.args[0]['spec']['template']['metadata'])
        current['spec']['template']['spec']['containers'][0]['env'][0]['value']='new-owner-value'
        with patch.object(probe,'replace') as replace,self.assertRaises(ValueError):
            probe.recover_reviewed_name(current,expected,'unused')
        replace.assert_not_called()

    def test_unrelated_writer_cannot_be_overwritten(self):
        original=service();candidate=probe.probe_document(original,'worker-stg','123-1')
        current=deepcopy(candidate);current['spec']['template']['spec']['containers'][0]['env'][0]['value']='new-owner-value'
        with self.assertRaises(RuntimeError): probe.restored_document(original,current,candidate)

    def test_api_omits_only_zero_percent_without_weakening_full_spec_comparison(self):
        original=service();candidate=probe.probe_document(original,'worker-stg','123-1')
        current=deepcopy(candidate);current['spec']['traffic'][1].pop('percent')
        self.assertEqual(probe.restored_document(original,current,candidate)['spec']['template'],original['spec']['template'])
        current['spec']['traffic'][1]['tag']='another-owner-tag'
        with self.assertRaises(RuntimeError): probe.restored_document(original,current,candidate)

    def test_reviewed_prior_probe_recovery_requires_complete_original_spec_hash(self):
        original=service();original['spec']['template']['metadata'].pop('name')
        original['spec']['traffic']=[{'revisionName':'worker-stg-00001-abc','percent':100}]
        current=probe.probe_document(original,'worker-stg','123-1')
        expected={'image':probe.image_and_revision(original)[0],'revision':'worker-stg-00001-abc',
            'spec_sha256':probe.spec_hash(original),'recover_from_sha256':probe.spec_hash(current),
            'recover_profile_revision':'worker-stg-profile-123-1'}
        with patch.object(probe,'replace') as replace,patch.object(probe,'describe',return_value=original):
            probe.recover_reviewed_name(current,expected,'unused')
            self.assertEqual(replace.call_args.args[0]['spec'],original['spec'])

    def test_local_execution_is_rejected(self):
        with patch.dict(os.environ,{},clear=True), self.assertRaises(ValueError), patch.object(probe,'cli') as cli:
            probe.require_actions({},dict.fromkeys(probe.TARGETS))
        cli.assert_not_called()

    def test_workflow_is_manual_owner_staging_only_with_independent_restore(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/profile-stg-startup.yml').read_text()
        self.assertIn('workflow_dispatch:',text)
        for event in ('push:','pull_request:','schedule:','repository_dispatch:'): self.assertNotIn(event,text)
        self.assertIn("github.ref == 'refs/heads/develop' && github.actor == github.repository_owner",text)
        self.assertIn('group: deploy-stg',text)
        self.assertIn('--profile --output',text)
        self.assertIn('--restore --output',text)
        self.assertIn('if: always()',text)
        self.assertNotIn('startup-profile-private\n',text)

    def test_owner_source_and_signed_receipt_gate(self):
        env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'mmorinag127/sawa-hospital-system',
             'GITHUB_REF':'refs/heads/develop','GITHUB_ACTOR':'owner','GITHUB_REPOSITORY_OWNER':'owner','GITHUB_SHA':'b'*40}
        expected=dict.fromkeys(probe.TARGETS,{})
        payload={'kind':'stg-startup-profile','source_sha':'b'*40,'inventory_sha256':probe.sha256(probe.canonical(expected)).hexdigest(),
                 'script_sha256':probe.sha256(Path(probe.__file__).read_bytes()).hexdigest(),'test_exit_code':0,'linux_launcher_exit_code':0}
        with patch.dict(os.environ,env,clear=True), patch.object(probe,'verify_signed',return_value=payload):
            probe.require_actions({},expected)
            for key,value in [('source_sha','c'*40),('test_exit_code',False),('test_exit_code',1),('linux_launcher_exit_code',1),('script_sha256','a'*64)]:
                bad={**payload,key:value}
                with patch.object(probe,'verify_signed',return_value=bad),self.assertRaises(ValueError): probe.require_actions({},expected)

    def test_gcloud_error_does_not_leak_secret_export(self):
        failure=type('Result',(),{'returncode':1,'stdout':'must-not-print','stderr':'must-not-print'})()
        with patch.object(probe.subprocess,'run',return_value=failure),self.assertRaises(RuntimeError) as error:
            probe.cli('run','services','replace','private.json')
        self.assertNotIn('must-not-print',str(error.exception))

    def test_private_export_removed_on_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(probe,'cli',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError): probe.replace(service(),directory)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_restore_original_is_noop(self):
        original=service();candidate=probe.probe_document(original,'worker-stg','123-1')
        with patch.object(probe,'describe',return_value=original), patch.object(probe,'replace') as replace:
            probe.restore(original,candidate,'unused')
            replace.assert_not_called()

    def test_full_profile_restore_all_three_without_secret_output(self):
        states={name:service(name) for name in probe.TARGETS}
        expected={name:{'image':probe.image_and_revision(doc)[0],'revision':probe.image_and_revision(doc)[1],'spec_sha256':probe.spec_hash(doc)} for name,doc in states.items()}
        before=deepcopy(states)
        def replace(doc,_):
            doc=deepcopy(doc);name=doc['metadata']['name'];rev=doc['spec']['template']['metadata']['name']
            doc['status']={'latestReadyRevisionName':rev,'traffic':[{'revisionName':before[name]['status']['latestReadyRevisionName'],'percent':100}]}
            if '-profile-' in rev:
                doc['status']['traffic'].append({'revisionName':rev,'percent':0,'tag':'startup-profile',
                    'url':'https://startup-profile---'+name+'-example.a.run.app'})
            states[name]=doc
        capture=io.StringIO()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,{'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}), \
             patch.object(probe,'describe',side_effect=lambda name:deepcopy(states[name])), patch.object(probe,'replace',side_effect=replace), \
             patch.object(probe,'cli') as cli,patch.object(probe.urllib.request,'urlopen') as request,contextlib.redirect_stdout(capture):
            request.return_value.__enter__.return_value.status=200
            output=Path(directory)/'public'
            probe.profile(expected,output)
            for name in states:
                self.assertEqual(probe.spec_hash(states[name]),probe.spec_hash(before[name]))
                self.assertTrue(json.loads((output/(name+'-status.json')).read_text())['restored'])
            self.assertEqual(list((Path(directory)/'startup-profile-private').iterdir()),[])
            cli.assert_not_called()
        self.assertNotIn('must-not-print',capture.getvalue())

if __name__=='__main__': unittest.main()
