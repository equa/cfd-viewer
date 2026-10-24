# equa-cfd-viewer documentation

This repository's documentation and its OpenSpec artifacts live in
**equa-platform-docs**, under
`docs/platform/modules/cfd-viewer/`.

They are tracked there rather than here so they have one home with review,
history and lint, and so the wiki publishes them from a tracked tree instead of
a copy taken at publish time.

- **specs/** — capability specifications.
- **changes/** — active OpenSpec changes; completed ones move to
  `changes/archive/`.

`openspec/specs` and `openspec/changes` in this repository are symlinks into
that tree, so the `openspec` CLI works here as usual when
`equa-platform-docs` is checked out beside this repository. They dangle
otherwise, which no lint or build step treats as an error.
