# Ruling slant before row detection

## Reported Source

- stg order ORDe6a981a7, FAC00014, 2026-11-15 through 2026-11-21.
- PDF SHA256 b3e734ab520dd949279208af03e9b7541aaa948075527f7fc88698fe2c6d21c9.
- Actual UI: 61 detected body rows versus 56 authored rows; five height outliers.
- Latest job failed with fax_physical_body_count_changed; no saved geometry overrides.

## Cause And Changes

- Row count validation preceded bounded slant correction. A failure prevented that correction from running.
- Both axis review screens used the raw rectified image, independently of the later OCR correction.
- Long observed ink segments now supply a Y-dependent slant field before axis detection. No menu count or expected spacing supplies the fit.
- Shared preparation is used by OCR preprocessing, step-review output, and operator axis review.
- A nonmonotonic displacement map is rejected. Missing line evidence does not manufacture geometry.
- Legacy saved operator Y coordinates keep their original canvas. New corrections persist the canvas version returned by the server review.
- Original PDFs, operator quantity values and saved sheets remain unchanged. No production deployment.

## Verification

Implementation complete; final serial batch and exact-order staging flow pending. No success claim from the prior different-original validation.
