"""API boundary faults only; actual six-copy output proof is recorded separately."""
import pytest
from fastapi import HTTPException
from src.api import outputs
from src.services import output_builder


@pytest.mark.parametrize('download',[outputs.download_labels,outputs.download_delivery,outputs.download_aggregate])
def test_missing_output_configuration_is_an_input_rejection(monkeypatch,download):
    def fail(*args,**kwargs):raise ValueError('delivery_quantity_mapping_unresolved')
    monkeypatch.setattr(output_builder,'build_output_preview',fail)
    with pytest.raises(HTTPException) as caught:
        download('synthetic')
    assert caught.value.status_code==400
    assert caught.value.detail=='delivery_quantity_mapping_unresolved'


@pytest.mark.parametrize('download',[outputs.download_labels,outputs.download_delivery,outputs.download_aggregate])
def test_unexpected_output_failure_is_not_misclassified_as_missing_input(monkeypatch,download):
    def fail(*args,**kwargs):raise RuntimeError('unexpected')
    monkeypatch.setattr(output_builder,'build_output_preview',fail)
    with pytest.raises(RuntimeError,match='unexpected'):
        download('synthetic')


@pytest.mark.parametrize('kind',['labels','delivery','aggregate'])
def test_preview_siblings_keep_the_same_input_error_boundary(monkeypatch,kind):
    def fail(*args,**kwargs):raise ValueError('delivery_quantity_mapping_unresolved')
    monkeypatch.setattr(output_builder,'build_output_preview',fail)
    monkeypatch.setattr(output_builder,'build_delivery_preview',fail)
    with pytest.raises(HTTPException) as caught:
        outputs.preview_output('synthetic',kind)
    assert caught.value.status_code==400
