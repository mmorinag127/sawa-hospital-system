"""Learned-head mechanism tests and explicit private-input native validation."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import ast
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import torch
from torch import nn

from src.services import hakodate_empty_sequence_model as adapter


class SmallForward(nn.Module):
    def __init__(self, failure=False, omit=False):
        super().__init__()
        self.head = nn.Linear(2, 3)
        self.refine_iters = 1
        self.tokenizer = object()
        self.failure, self.omit = failure, omit
        self.active = self.maximum = 0

    def forward(self, images, max_length=None):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            time.sleep(.005)
            logits = torch.zeros((*images.shape[:2], 3)) if self.omit else self.head(images)
            if self.failure:
                raise RuntimeError("authored forward failure")
            return logits
        finally:
            self.active -= 1


class LearnedHeadMechanism(unittest.TestCase):
    def wrap(self, base):
        return adapter.InitialEosPARSeq(base, torch.tensor([2., 3.]), torch.tensor(.25), {})

    def test_only_initial_eos_changes_and_base_state_is_unchanged(self):
        base = SmallForward()
        wrapped = self.wrap(base)
        images = torch.arange(12, dtype=torch.float32).reshape(2, 3, 2)
        state = {name:value.clone() for name,value in base.state_dict().items()}
        before, after = base(images), wrapped(images)
        self.assertTrue(torch.equal(before[:, :, 1:], after[:, :, 1:]))
        self.assertTrue(torch.equal(before[:, 1:, 0], after[:, 1:, 0]))
        self.assertTrue(torch.equal(after[:, 0, 0], images[:, 0] @ wrapped.eos_weight + wrapped.eos_bias))
        self.assertTrue(all(torch.equal(value, base.state_dict()[name]) for name,value in state.items()))
        self.assertEqual(len(base.head._forward_pre_hooks), 0)

    def test_exception_and_missing_final_head_remove_only_owned_hook(self):
        for kwargs, error in [({'failure':True}, RuntimeError), ({'omit':True}, ValueError)]:
            base = SmallForward(**kwargs)
            observer = base.head.register_forward_pre_hook(lambda *_: None)
            wrapped = self.wrap(base)
            with self.assertRaises(error):
                wrapped(torch.ones(1, 2, 2))
            self.assertEqual(len(base.head._forward_pre_hooks), 1)
            observer.remove()

    def test_concurrent_calls_are_serialized_without_cross_call_features(self):
        base = SmallForward()
        wrapped = self.wrap(base)
        inputs = [torch.full((1, 3, 2), float(index)) for index in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(wrapped, inputs))
        self.assertEqual(base.maximum, 1)
        for images, output in zip(inputs, outputs):
            self.assertTrue(torch.equal(output[:, 0, 0], images[:, 0] @ wrapped.eos_weight + wrapped.eos_bias))
        self.assertEqual(len(base.head._forward_pre_hooks), 0)

    def test_mutated_parameters_adapter_and_refinement_fail_closed(self):
        for change in ('base', 'adapter', 'refinement', 'training'):
            base = SmallForward()
            wrapped = self.wrap(base)
            with torch.no_grad():
                if change == 'base':
                    base.head.weight.add_(1)
                elif change == 'adapter':
                    wrapped.eos_weight.add_(1)
                elif change == 'refinement':
                    base.refine_iters = 2
                else:
                    wrapped.train()
            with self.assertRaisesRegex(ValueError, 'model_changed'):
                wrapped(torch.ones(1, 2, 2))
            self.assertEqual(len(base.head._forward_pre_hooks), 0)

    def test_packaged_adapter_and_provenance_tampering_fail_closed(self):
        tensors, provenance = adapter._load_adapter(adapter.ARTIFACT_DIR)
        self.assertEqual(provenance['base_sha256'], adapter.BASE_SHA256)
        self.assertEqual(provenance['adapter_sha256'], adapter.ADAPTER_SHA256)
        self.assertEqual(tuple(tensors['eos_weight'].shape), (512,))
        for filename in ('adapter.safetensors','provenance.json'):
            with TemporaryDirectory() as root:
                root = Path(root)
                for name in ('adapter.safetensors','provenance.json'):
                    content = (adapter.ARTIFACT_DIR/name).read_bytes()
                    (root/name).write_bytes(content + (b'changed' if name == filename else b''))
                with self.assertRaisesRegex(ValueError, 'adapter_integrity'):
                    adapter._load_adapter(root)

    def test_loader_pins_download_and_rejects_integrity_before_construction(self):
        import yomitoku

        # Synthetic loader boundaries, not native recognition evidence.
        for digest, keys in [('wrong', ([], [])), (adapter.BASE_SHA256, (['missing'], [])),
                             (adapter.BASE_SHA256, ([], ['extra']))]:
            with patch.object(adapter, 'hf_hub_download', return_value='pinned') as download, \
                    patch.object(adapter, 'file_sha256', return_value=digest), \
                    patch.object(yomitoku, 'TextRecognizer') as constructor, \
                    patch.object(adapter, 'load_model', return_value=keys), \
                    patch.object(adapter, 'install_empty_sequence_model') as install:
                with self.assertRaises(ValueError):
                    adapter.load_empty_sequence_recognizer('cpu')
                download.assert_called_once_with(adapter.MODEL_REPO, 'model.safetensors', revision=adapter.MODEL_REVISION)
                if digest == 'wrong':
                    constructor.assert_not_called()
                else:
                    constructor.assert_called_once_with(device='cpu', visualize=False, from_pretrained=False)
                install.assert_not_called()

    def test_runtime_metrics_preserve_real_metadata_without_fabricating_mock_provenance(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {'DB_URI': 'sqlite:///' + str(Path(root)/'unused.sqlite3')}):
            from src.hakodate_best_method_runtime import run_text_recognizer_corner_noise_trial as runtime

        metadata = {'base_sha256': adapter.BASE_SHA256, 'adapter_sha256': adapter.ADAPTER_SHA256}
        for recognizer in (object(), SimpleNamespace(recognition_model_metadata=metadata)):
            _, metrics = runtime.run_text_recognizer_direct(recognizer=recognizer,
                contact_sheet=Image.new('RGB', (1,1)), regions=[], polygons=[],
                score_threshold=.45, digit_score_threshold=.05, candidate_digit_score_threshold=.05,
                enable_context_repair=False)
            if hasattr(recognizer, 'recognition_model_metadata'):
                self.assertEqual(metrics['recognition_model_metadata'], metadata)
                self.assertIsNot(metrics['recognition_model_metadata'], metadata)
                self.assertEqual(json.loads(json.dumps(metrics))['recognition_model_metadata'], metadata)
            else:
                self.assertNotIn('recognition_model_metadata', metrics)

    def test_renderer_canonical_metrics_propagate_metadata_without_operator_fabrication(self):
        source = Path(__file__).resolve().parents[2]/'src/hakodate_best_method_runtime/render_best_method_overlay_all_facilities.py'
        tree = ast.parse(source.read_text())
        # Execute the authored canonical-metrics unpack expression, not OCR mocks.
        expression, = [node for node in ast.walk(tree) if isinstance(node, ast.IfExp)
            and isinstance(node.body, ast.Dict)
            and any(isinstance(key, ast.Constant) and key.value == 'recognition_model_metadata' for key in node.body.keys)]
        code = compile(ast.Expression(expression), str(source), 'eval')
        metadata = {'base_sha256':adapter.BASE_SHA256, 'adapter_sha256':adapter.ADAPTER_SHA256}
        self.assertEqual(eval(code, {'ocr_metrics':{}}), {})
        actual = eval(code, {'ocr_metrics':{'recognition_model_metadata':metadata}})
        self.assertEqual(json.loads(json.dumps(actual)), {'recognition_model_metadata':metadata})


@unittest.skipUnless(os.environ.get('HAKODATE_EMPTY_SEQUENCE_INPUT_DIR'), 'explicit native private inputs required')
class NativeLearnedHead(unittest.TestCase):
    def test_native_396_inputs_and_exact_frozen_learned_artifact(self):
        from importlib.metadata import version

        self.assertEqual(version('yomitoku'), '0.9.5')
        torch.set_num_threads(2)
        root = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_INPUT_DIR'])
        weights = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_BASE_WEIGHTS'])
        learned_path = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_LEARNED_SOURCE'])
        ones_root = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_ONES_DIR'])
        diagnostic = json.loads(Path(os.environ['HAKODATE_EMPTY_SEQUENCE_DIAGNOSTIC']).read_text())
        self.assertEqual(diagnostic['base_sha256'], adapter.BASE_SHA256)
        self.assertEqual(diagnostic['learned_eos_sha256'], adapter.LEARNED_SHA256)
        native_ones = {row['cell']:row for row in diagnostic['comparisons'][-4:]}
        self.assertEqual(set(native_ones), {'K21','I24','I31','I45'})
        self.assertEqual(adapter.file_sha256(weights), adapter.BASE_SHA256)
        self.assertEqual(adapter.file_sha256(learned_path), adapter.LEARNED_SHA256)
        learned = torch.load(learned_path, weights_only=True, map_location='cpu')
        tensors, _ = adapter._load_adapter(adapter.ARTIFACT_DIR)
        self.assertTrue(torch.equal(tensors['eos_weight'], learned['eos_weight']))
        self.assertTrue(torch.equal(tensors['eos_bias'], learned['eos_bias']))
        with patch.object(adapter, 'hf_hub_download', wraps=adapter.hf_hub_download) as download:
            recognizer = adapter.load_empty_sequence_recognizer('cpu')
            download.assert_called_once_with(adapter.MODEL_REPO, 'model.safetensors', revision=adapter.MODEL_REVISION)
        wrapped = recognizer.model
        base = wrapped.base
        recognizer.model = base
        with patch.object(adapter, 'version', return_value='0.12.0'):
            with self.assertRaisesRegex(ValueError, 'native_architecture'):
                adapter.install_empty_sequence_model(recognizer, weights_path=weights)
        for field, value in [('refine_iters', 0), ('decode_ar', False), ('export_onnx', True)]:
            previous = getattr(base, field)
            setattr(base, field, value)
            with self.assertRaisesRegex(ValueError, 'native_architecture'):
                adapter.install_empty_sequence_model(recognizer, weights_path=weights)
            setattr(base, field, previous)
        with TemporaryDirectory() as temporary:
            corrupt = Path(temporary)/'wrong.safetensors'
            corrupt.write_bytes(b'not native weights')
            with self.assertRaisesRegex(ValueError, 'base_integrity'):
                adapter.install_empty_sequence_model(recognizer, weights_path=corrupt)
        with torch.no_grad():
            original_bias = base.head.bias[0].clone()
            base.head.bias[0].add_(1)
            with self.assertRaisesRegex(ValueError, 'native_weights'):
                adapter.install_empty_sequence_model(recognizer, weights_path=weights)
            base.head.bias[0].copy_(original_bias)
        before_state = tuple((name,tensor._version) for name,tensor in base.named_parameters())
        adapter.install_empty_sequence_model(recognizer, weights_path=weights)
        wrapped = recognizer.model
        self.assertEqual(recognizer.recognition_model_metadata['base_sha256'], adapter.BASE_SHA256)
        self.assertEqual(recognizer.recognition_model_metadata['adapter_sha256'], adapter.ADAPTER_SHA256)
        rows = []
        for directory, image_file, records_file, selected in (
            (root, 'recognizer-contact.png', 'recognizer-records.json', None),
            (ones_root, 'best_method_contact_sheet.png', 'best_method_records.json', {'K21','I24','I31','I45'}),
        ):
            pixels = cv2.imread(str(directory/image_file))
            records = json.loads((directory/records_file).read_text())
            for record in records:
                if selected is not None and record['sheet_cell'] not in selected:
                    continue
                x0,y0,x1,y1 = record['ocr_contact_crop_box']
                crop = pixels[y0:y1,x0:x1].copy()
                loader, points = recognizer.preprocess(crop, None)
                self.assertEqual(len(loader), 1)
                self.assertEqual(loader[0].shape[0], 1)
                baseline_raw = record['recognizer_raw_text'] if selected is None else native_ones[record['sheet_cell']]['before']
                rows.append((record, crop, loader[0][0], points, baseline_raw))
        self.assertEqual(len(rows), 396)
        report = {'base_sha256':adapter.BASE_SHA256, 'adapter_sha256':adapter.ADAPTER_SHA256,
                  'native_version':version('yomitoku'), 'comparisons':[]}
        with torch.inference_mode():
            for offset in range(0, len(rows), 12):
                batch = rows[offset:offset+12]
                inputs = torch.stack([row[2] for row in batch])
                baseline = base(inputs)
                logits = wrapped(inputs)
                self.assertEqual(logits.shape, baseline.shape)
                self.assertTrue(torch.equal(logits[:, :, 1:], baseline[:, :, 1:]))
                self.assertTrue(torch.equal(logits[:, 1:, 0], baseline[:, 1:, 0]))
                before, before_scores = recognizer.tokenizer.decode(baseline.softmax(-1))
                after, after_scores = recognizer.tokenizer.decode(logits.softmax(-1))
                for row, raw_before, raw_after, score_before, score_after in zip(batch,before,after,before_scores,after_scores):
                    old, crop, tensor, _, baseline_raw = row
                    cell = old['sheet_cell']
                    white = bool((crop == 255).all())
                    self.assertEqual(raw_before, baseline_raw)
                    self.assertEqual(raw_after, '' if white else raw_before)
                    if cell in {'E31','E63'} and offset < 392:
                        self.assertEqual(raw_after,'26')
                    report['comparisons'].append({'index':len(report['comparisons']), 'cell':cell,
                        'crop_sha256':sha256(crop.tobytes()).hexdigest(),
                        'input_sha256':sha256(tensor.numpy().tobytes()).hexdigest(),
                        'input_shape':list(tensor.shape), 'white':white, 'before':raw_before,'after':raw_after,
                        'before_score':float(score_before), 'after_score':float(score_after)})
        self.assertEqual(sum(row['white'] for row in report['comparisons']), 325)
        self.assertEqual([row['after'] for row in report['comparisons'][-4:]], ['1']*4)
        self.assertEqual([row['after'] for row in report['comparisons']],
                         [row['after'] for row in diagnostic['comparisons']])
        report['synthetic_digits'] = []
        for raw in ('0','1','26'):
            image = Image.new('RGB', (128,80), 'white')
            font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial Bold.ttf', 44)
            ImageDraw.Draw(image).text((64,40), raw, anchor='mm', font=font, fill='black')
            pixels = np.asarray(image)[:, :, ::-1].copy()
            loader, points = recognizer.preprocess(pixels, None)
            with torch.inference_mode():
                before, _, _ = recognizer.postprocess(base(loader[0]).softmax(-1), points)
                after, _, _ = recognizer.postprocess(wrapped(loader[0]).softmax(-1), points)
            self.assertEqual(after, before)
            report['synthetic_digits'].append({'label':raw,'before':before[0],'after':after[0]})
        zero_root = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_ZEROS_DIR'])
        zero_contact = cv2.imread(str(zero_root/'best_method_contact_sheet.png'))
        zero_records = json.loads((zero_root/'best_method_records.json').read_text())
        self.assertEqual(adapter.file_sha256(zero_root/'best_method_contact_sheet.png'),
                         'cc57cbe04cbb0e37a517ef7075d1d8f5fefa9b7e875d11738b06ba2241f33047')
        audit_pdf = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_H35_SOURCE_PDF'])
        self.assertEqual(adapter.file_sha256(audit_pdf),
                         'dfe603dde35472382ff074b13b00d52392473564a878b677f255e3ce14c3a140')
        report['semantic_zero_label_candidates'] = []
        for row in zero_records:
            if row['truth']['expected_text'] != '0':
                continue
            x0,y0,x1,y1 = row['ocr_contact_crop_box']
            crop = zero_contact[y0:y1,x0:x1].copy()
            loader, points = recognizer.preprocess(crop, None)
            with torch.inference_mode():
                before, _, _ = recognizer.postprocess(base(loader[0]).softmax(-1), points)
                after, _, _ = recognizer.postprocess(wrapped(loader[0]).softmax(-1), points)
            report['semantic_zero_label_candidates'].append({'cell':row['sheet_cell'], 'before':before[0], 'after':after[0],
                'truth_expected_text':row['truth']['expected_text'],
                'white':bool((crop == 255).all()), 'crop_sha256':sha256(crop.tobytes()).hexdigest()})
        self.assertEqual(len(report['semantic_zero_label_candidates']),24)
        report['h35_source_audit'] = {'classification':'blank with printed grid fragments; parent visually audited',
            'source_pdf':str(audit_pdf), 'source_pdf_sha256':adapter.file_sha256(audit_pdf),
            'contact_sha256':adapter.file_sha256(zero_root/'best_method_contact_sheet.png')}
        real_root = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_REAL_ZEROS_DIR'])
        real_pdf = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_REAL_ZEROS_PDF'])
        self.assertEqual(adapter.file_sha256(real_pdf),
                         '4cd10eb87fdbbb16f327c9513f07ab428344b2d137a112ba5033d184f9ba8289')
        self.assertEqual(adapter.file_sha256(real_root/'best_method_contact_sheet.png'),
                         '1686b335c7f4989108fe8a86151d3a58e5161828e1fdb0681b4414442d66d66a')
        real_pixels = cv2.imread(str(real_root/'best_method_contact_sheet.png'))
        real_rows = {row['sheet_cell']:row for row in json.loads((real_root/'best_method_records.json').read_text())}
        report['real_zero_inputs'] = []
        for cell in ('F11','E12','F12','E16','F16','F17'):
            row = real_rows[cell]
            self.assertEqual(row['recognizer_raw_text'],'0')
            x0,y0,x1,y1 = row['ocr_contact_crop_box']
            crop = real_pixels[y0:y1,x0:x1].copy()
            loader, points = recognizer.preprocess(crop,None)
            with torch.inference_mode():
                before, _, _ = recognizer.postprocess(base(loader[0]).softmax(-1),points)
                after, _, _ = recognizer.postprocess(wrapped(loader[0]).softmax(-1),points)
            report['real_zero_inputs'].append({'cell':cell,'before':before[0],'after':after[0],
                'crop_sha256':sha256(crop.tobytes()).hexdigest()})
        self.assertEqual(before_state, tuple((name,tensor._version) for name,tensor in base.named_parameters()))
        self.assertEqual(len(base.head._forward_pre_hooks), 0)
        output = Path(os.environ['HAKODATE_EMPTY_SEQUENCE_REPORT'])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n')
        for row in report['semantic_zero_label_candidates']:
            if row['cell'] == 'H35':
                self.assertEqual(row['crop_sha256'],'975a3e6290448f3cc00e45b3b6c6fcb52b086b8ebc5530dbed656a454c271276')
                self.assertEqual((row['before'],row['after']),('1',''))
            else:
                self.assertEqual(row['after'],row['before'],row['cell'])
        self.assertEqual([(r['cell'],r['before'],r['after']) for r in report['semantic_zero_label_candidates']
                          if r['before'] != r['after']], [('H35','1','')])
        for row in report['real_zero_inputs']:
            self.assertEqual((row['before'],row['after']),('0','0'),row['cell'])
        self.assertEqual(len(report['real_zero_inputs']),6)
        print('native full-forward: 396 inputs; white325 empty; nonwhite71 unchanged; real1=4 retained', flush=True)


if __name__ == '__main__':
    unittest.main()
