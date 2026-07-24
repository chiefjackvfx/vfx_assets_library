# Contextual Names for Numeric Stock Clips

## Summary

Generate descriptive names for Stock clips whose filename stem is numeric-only.

Example:

```text
/001test/Light Leaks/Blue wipe/5.mov
→ Light_Leaks_Blue_wipe_05.mov
```

The selected scan root (`001test`) is excluded. Source files remain untouched; naming applies to the review candidate and managed library files.

## Implementation Changes

- Detect numeric-only stems after trimming whitespace; names containing meaningful text remain unchanged.
- Build the inferred name from every parent folder below the selected scan root, ordered root-to-leaf, followed by the clip number.
- Pad numbers to at least two digits while preserving wider numbers:
  - `1` → `01`
  - `10` → `10`
  - `001` → `001`
- Normalize folder components using portable naming rules and remove ordering prefixes such as `08.` or `1 -`.
- Keep the candidate display name readable with spaces; the existing managed filename sanitizer produces underscore-separated files.
- Apply the common name to the source, preview, thumbnail, and named manifest:
  - `Light_Leaks_Blue_wipe_05.mov`
  - `Light_Leaks_Blue_wipe_05_Preview.mp4`
  - `Light_Leaks_Blue_wipe_05_Thumbnail.jpg`
  - `Light_Leaks_Blue_wipe_05.json`
- Perform preview matching and taxonomy classification from original source paths before applying the inferred display name.
- Preserve user edits in the review screen as the final naming authority.
- Continue using `_2`, `_3`, and subsequent suffixes for genuine managed-library collisions.
- Do not rename existing imported Stock assets automatically.

## Interfaces and Tests

- Add a reusable Stock-name inference helper accepting the source path and selected scan root.
- Test numeric-only detection, two-digit padding, already-padded numbers, nested folders, ordering-prefix cleanup, Unicode folder names, and portable sanitization.
- Confirm semantic names such as `05 - Meteors.mov` and `Blast_01.mov` remain unchanged.
- Confirm the selected root is excluded and files directly beneath it remain numbered names.
- Scan `/home/gambit/001test` and verify examples including:
  - `Light_Leaks_Blue_wipe_05`
  - `Light_Leaks_Warm_Wash_01`
  - `Light_Leaks_Warm_Wipe_11`
- Verify preview pairing, taxonomy inference, flat import layout, duplicate handling, and source-byte preservation remain unchanged.

## Assumptions

- This applies only to future Stock scans.
- “Numeric-only” means the complete filename stem contains digits and optional surrounding whitespace, with no other tokens.
- All meaningful nested folders below the selected root contribute to the generated name.
