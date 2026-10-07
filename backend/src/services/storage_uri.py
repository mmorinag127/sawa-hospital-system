"""Load the same stdlib-only URI codec shipped in both deployment contexts."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

# Source checkout: backend/ beside ocr_pipeline/. Prepared backend image:
# /app/src/ and /app/ocr_pipeline/ (prepare_hakodate_backend_deploy_source.sh).
_root = Path(__file__).resolve().parents[2]
if _root.name == "backend":
    _root = _root.parent
_spec = spec_from_file_location("canonical_storage_uri", _root / "ocr_pipeline/app/storage_uri.py")
if _spec is None or _spec.loader is None:
    raise ImportError("canonical storage URI codec is required")
_codec = module_from_spec(_spec)
_spec.loader.exec_module(_codec)
format_gcs_uri = _codec.format_gcs_uri
parse_gcs_uri = _codec.parse_gcs_uri
validate_generation = _codec.validate_generation
signing_query = _codec.signing_query
_markup_spec = spec_from_file_location("canonical_figure_markup", _root / "ocr_pipeline/app/figure_markup.py")
if _markup_spec is None or _markup_spec.loader is None:
    raise ImportError("canonical figure markup parser is required")
_markup = module_from_spec(_markup_spec)
_markup_spec.loader.exec_module(_markup)
image_references = _markup._extract_markdown_images
replace_image_references = _markup._replace_markdown_images
