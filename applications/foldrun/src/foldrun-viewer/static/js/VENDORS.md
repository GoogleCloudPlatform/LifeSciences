# Vendored JavaScript Libraries

These files are bundled locally to eliminate runtime CDN dependencies.
The viewer runs in GCP VPCs where egress to external CDNs may be restricted.

| File | Library | Version | Source |
|---|---|---|---|
| `3Dmol-min.js` | 3Dmol.js | latest at time of download | https://3Dmol.csb.pitt.edu/build/3Dmol-min.js |
| `marked.min.js` | marked | 11.1.1 | https://cdn.jsdelivr.net/npm/marked@11.1.1/marked.min.js |

## Updating

To update a library, download the new version from the source URL above
and replace the file. Update the version in this table accordingly.
