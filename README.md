# skill-hash

A platform-neutral CLI tool for computing and verifying canonical integrity
digests over collections of Kagenti skill files and folders.

Designed for use in two contexts:

- **Developer workstation / CI pipeline** — compute a digest over a set of
  skill files before deploying, and save it as a Kubernetes Deployment
  annotation.
- **Kubernetes init container** — re-compute the digest at pod startup and
  compare it against the developer-provided annotation value. On match,
  attest the result to an in-cluster Sigstore service (RHTAS). On mismatch,
  exit 1 so the pod fails and the main agent container never starts.

---

## Project Structure

```
skill-hash/
├── Dockerfile              # Multi-arch container image
├── pyproject.toml          # Python package metadata
├── deployment.yaml         # Example Kubernetes Deployment manifest
├── README.md
└── src/
    └── skill_hash/
        ├── __init__.py
        └── cli.py          # CLI implementation
```

---

## Design Decisions

| Property | Implementation |
|---|---|
| **Digest algorithm** | SHA-256 (via Python `hashlib` — no shell dependencies) |
| **Line ending normalization** | CRLF → LF always applied before hashing |
| **File sort order** | UTF-8 byte encoding of path string — locale-neutral |
| **Path representation** | Relative to explicit `--root` — mount-point-neutral |
| **Path separator** | Comma `,` — paths must not contain commas |
| **Multi-arch image** | `linux/amd64`, `linux/arm64`, `linux/s390x`, `linux/ppc64le` |
| **Base image** | `python:3.11-slim` — minimal, multi-arch |
| **Runtime user** | UID 1001 — non-root |
| **Locale enforcement** | `LC_ALL=C.UTF-8` in Dockerfile |

### Why relative paths?

Absolute paths embed the mount point into the manifest. If the developer
mounts skill files at `/skills` locally but the init container mounts them at
`/app/skills`, the manifest digest would differ even though the file contents
are identical. Relative paths anchored to an explicit `--root` eliminate this
dependency.

### Why comma-separated paths?

Newline-separated paths require careful handling across env vars, shell
interpolation, and Kubernetes Downward API injection. Comma separation is
unambiguous, single-line, and maps cleanly to a Kubernetes annotation value
without multiline YAML block scalars.

---

## Installation (local development)

```bash
pip install -e ".[dev]"
# or
pip install click>=8.1
pip install -e .
```

---

## CLI Reference

### `skill-hash compute`

Compute a canonical digest for a collection of skill files/folders.

```
skill-hash compute [OPTIONS] [PATHS]...

Options:
  --root TEXT              Root directory for relative path computation.
                           Can be set via SKILL_ROOT env var.  [required]
  --root-file PATH         Read root path from a file (Downward API mount).
                           Takes precedence over --root / SKILL_ROOT.
  -o, --output [digest|manifest|json]
                           Output format. [default: digest]
  --paths-from-env TEXT    Read comma-separated paths from SKILL_PATHS env var.
  --paths-from-file PATH   Read comma-separated paths from a file.
                           Takes precedence over --paths-from-env / SKILL_PATHS.
```

**Output formats:**

- `digest` — prints only the collection digest (default):
  ```
  sha256:a3f1c2d9e4b7cc821fdd440100f2938471abc...
  ```
- `manifest` — prints the full per-file manifest:
  ```
  a3f1c2...  core/summarize.py
  b7d9e4...  core/search.py
  cc821f...  tools/fetch.py
  ```
- `json` — prints both as JSON:
  ```json
  {
    "collectionDigest": "sha256:a3f1c2...",
    "root": "/skills",
    "files": [
      { "relativePath": "core/summarize.py", "digest": "sha256:a3f1c2..." },
      ...
    ]
  }
  ```

### `skill-hash verify`

Verify that a skill collection matches an expected digest.

Exits with code **0** on match, **1** on mismatch.

```
skill-hash verify [OPTIONS] [PATHS]...

Options:
  --root TEXT                  Root directory. Must match the root used
                               during compute. Can be set via SKILL_ROOT.
  --root-file PATH             Read root from a file. Takes precedence.
  --paths-from-env TEXT        Read paths from SKILL_PATHS env var.
  --paths-from-file PATH       Read paths from a file. Takes precedence.
  --expected-digest TEXT       Expected digest (sha256:...).
                               Can be set via EXPECTED_DIGEST env var.
  --expected-digest-file PATH  Read expected digest from a file.
                               Takes precedence over --expected-digest.
  --output-manifest PATH       Write the verified manifest to this path
                               on successful verification.
```

---

## Tested Behavior (Worked Examples)

The following test cases were run against the published image
`ghcr.io/webchang/skill-hash:0.1.0` using this repository's **own files** as
the skill collection. They are reproducible from a clean checkout.

> **Reproducibility note.** `skill-hash` hashes every file found under the
> given paths — including stray build artifacts such as `__pycache__/*.pyc`.
> To reproduce the digests below, hash a clean export of the source tree, not
> a working directory that may contain compiled files:
>
> ```bash
> git archive --format=tar HEAD | tar -x -C /tmp/skills-clean
> ```
>
> The digests below were computed at commit `f10f9b7` over the demo collection
> `src/` + `src/skill_hash/cli.py`. They will change whenever those files
> change (and the image tag is rebuilt) — regenerate them when that happens.

The demo collection is two representative paths: the **directory** `src/`
(recursed) and the **file** `src/skill_hash/cli.py`. In the examples,
`$REPO` is a clean export of the repository (see above) and `$DIGEST` holds
the collection digest from case 1.

### Case 1 — `compute` (digest)

```bash
podman run --rm -v "$REPO":/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  compute --root /skills /skills/src/,/skills/src/skill_hash/cli.py
# → sha256:42714020063673a53e9928a12a6dff42a2c9f5d17c5e3ed4e25f6ff6fa82bb13
```

### Case 2 — `compute` (manifest)

Per-file digests, sorted by UTF-8 byte order of the relative path. Note that
`src/skill_hash/cli.py` appears **once** even though it is named both directly
and via the `src/` directory — paths are deduplicated by relative path:

```bash
podman run --rm -v "$REPO":/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  compute --root /skills -o manifest /skills/src/,/skills/src/skill_hash/cli.py
```
```
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  src/skill_hash/__init__.py
cc57ef404df5aa5798e04f9cff67e7bb38b3560ef52e3295afecf8e143a96747  src/skill_hash/cli.py
```

(`__init__.py` is empty, so its digest is the well-known SHA-256 of zero
bytes, `e3b0c442…b855`.)

### Case 3 — `compute` (json)

```bash
podman run --rm -v "$REPO":/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  compute --root /skills -o json /skills/src/,/skills/src/skill_hash/cli.py
```
```json
{
  "collectionDigest": "sha256:42714020063673a53e9928a12a6dff42a2c9f5d17c5e3ed4e25f6ff6fa82bb13",
  "root": "/skills",
  "files": [
    { "relativePath": "src/skill_hash/__init__.py", "digest": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" },
    { "relativePath": "src/skill_hash/cli.py",      "digest": "sha256:cc57ef404df5aa5798e04f9cff67e7bb38b3560ef52e3295afecf8e143a96747" }
  ]
}
```

### Case 4 — `verify` matches (exit 0)

```bash
podman run --rm -v "$REPO":/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  verify --root /skills --expected-digest "$DIGEST" \
  /skills/src/,/skills/src/skill_hash/cli.py
# stderr: PASSED: digests match
echo $?   # → 0
```

### Case 5 — `verify` mismatch (exit 1)

```bash
podman run --rm -v "$REPO":/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  verify --root /skills \
  --expected-digest sha256:0000000000000000000000000000000000000000000000000000000000000000 \
  /skills/src/,/skills/src/skill_hash/cli.py
# stderr: FAILED: digest mismatch
echo $?   # → 1   (this is what fails the init container and blocks the pod)
```

### Case 6 — mount-point neutrality

Mounting the **same** tree at a **different** path (`/app/skills`) with a
matching `--root` yields the **identical** digest — manifest paths are
relative to `--root`, not absolute:

```bash
podman run --rm -v "$REPO":/app/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  compute --root /app/skills /app/skills/src/,/app/skills/src/skill_hash/cli.py
# → sha256:42714020063673a53e9928a12a6dff42a2c9f5d17c5e3ed4e25f6ff6fa82bb13  (same as Case 1)
```

### Case 7 — path outside `--root` is rejected (exit 1)

```bash
podman run --rm -v "$REPO":/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  compute --root /skills/src/skill_hash /skills/src/skill_hash/cli.py,/skills/pyproject.toml
# Error: Path '/skills/pyproject.toml' is not under the declared root '/skills/src/skill_hash'.
echo $?   # → 1
```

### Case 8 — empty directory is rejected (exit 1)

A declared directory containing no files is an error, not a silent no-op —
otherwise a digest could be computed over nothing:

```bash
mkdir -p "$REPO/empty"
podman run --rm -v "$REPO":/skills:ro ghcr.io/webchang/skill-hash:0.1.0 \
  compute --root /skills /skills/empty/
# Error: Directory contains no files: '/skills/empty'. ...
echo $?   # → 1
```

---

## Developer Workflow

### Step 1 — Compute the digest

Run the tool against your local skill files using the container image (ensures
identical algorithm to the init container):

```bash
DIGEST=$(docker run --rm \
  -v ./skills:/skills:ro \
  ghcr.io/webchang/skill-hash:0.1.0 \
  compute \
  --root /skills \
  /skills/core/,/skills/tools/fetch.py,/skills/rag/)

echo "$DIGEST"
# → sha256:a3f1c2d9e4b7cc821fdd440100f293847...
```

### Step 2 — Save digest as Deployment annotations

```bash
kubectl annotate deployment kagenti-agent \
  kagenti.io/skill-root="/skills" \
  kagenti.io/skill-paths="/skills/core/,/skills/tools/fetch.py,/skills/rag/" \
  kagenti.io/skill-collection-digest="$DIGEST" \
  kagenti.io/skill-digest-author="developer@example.com" \
  kagenti.io/skill-digest-timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --overwrite
```

### Step 3 — Apply the Deployment

```bash
kubectl apply -f deployment.yaml
```

---

## Init Container Workflow

The init container runs automatically as part of pod startup. No manual
intervention is needed. The sequence is:

```
Pod scheduled on node
  │
  ├─ Init container 1: skill-integrity-gate  (skill-hash:0.1.0)
  │    ├─ Reads skill-root, skill-paths, skill-collection-digest
  │    │   from Downward API volume mount (/pod-meta/)
  │    ├─ Re-computes digest using same algorithm
  │    ├─ MATCH   → writes /skill-signatures/skill-manifest.sha256
  │    │            exits 0 → proceeds to init container 2
  │    └─ MISMATCH → exits 1 → pod FAILS
  │                             main container NEVER starts
  │
  ├─ Init container 2: skill-attestor  (cosign / RHTAS)
  │    ├─ Fetches JWT-SVID from SPIRE Workload API
  │    ├─ Initializes cosign against in-cluster RHTAS TUF root
  │    ├─ Signs skill-manifest.sha256 with SPIFFE identity via Fulcio
  │    └─ Records bundle in Rekor transparency log → exits 0
  │
  └─ Main agent container starts normally
```

---

## Building the Multi-Arch Image

```bash
# Build for all supported architectures and push
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/s390x,linux/ppc64le \
  --tag ghcr.io/webchang/skill-hash:0.1.0 \
  --push \
  .
```

---

## Kubernetes Deployment Annotations

| Annotation | Description |
|---|---|
| `kagenti.io/skill-root` | Root directory for relative path computation |
| `kagenti.io/skill-paths` | Comma-separated list of skill file/folder paths |
| `kagenti.io/skill-collection-digest` | Developer-computed SHA-256 digest |
| `kagenti.io/skill-digest-author` | Who computed the digest (informational) |
| `kagenti.io/skill-digest-timestamp` | When the digest was computed (informational) |

---

## In-Cluster Sigstore Service (RHTAS)

The `skill-attestor` init container requires Red Hat Trusted Artifact Signer
(RHTAS) to be deployed on the cluster. RHTAS provides in-cluster Fulcio
(certificate authority) and Rekor (transparency log) services.

Install RHTAS via OperatorHub on OpenShift, then configure Fulcio to accept
SPIRE-issued JWT-SVIDs as the OIDC identity source:

```yaml
# Securesign CR — configure SPIRE as Fulcio OIDC issuer
spec:
  fulcio:
    config:
      OIDCIssuers:
        - issuer: >
            https://spire-oidc.zero-trust-workload-identity-manager
              .svc.cluster.local
          clientID: sigstore
          type: spiffe
          SPIFFETrustDomain: "your-trust-domain.local"
  rekor:
    enabled: true
  tuf:
    enabled: true
```

---

## Constraints

- Skill file and folder paths **must not contain commas** (used as separator).
- All skill paths **must reside under the declared `--root`** directory.
- The `--root` value **must be identical** between the `compute` invocation
  (developer) and the `verify` invocation (init container). Both read it from
  the `kagenti.io/skill-root` annotation via the Downward API.
- The init container mounts skill files at the **same path structure** as
  declared in `--root` and `--paths`. If files are mounted at a different
  prefix, update the annotation accordingly.

---

## Platform Neutrality

The image and algorithm are designed to produce identical digests regardless
of the platform on which they run:

| Source of variance | Mitigation |
|---|---|
| CPU architecture | Multi-arch image via `docker buildx` |
| Locale-dependent sort | Sort key is `path.encode('utf-8')` |
| Line ending differences (CRLF vs LF) | `content.replace(b'\r\n', b'\n')` always applied |
| Absolute vs relative paths | All manifest paths relative to explicit `--root` |
| Path canonicalization | `Path.resolve()` on every collected file |
| Manifest string encoding | Explicit `utf-8` throughout |
| OS-level locale | `ENV LC_ALL=C.UTF-8` in Dockerfile |
| Shell dependencies | None — pure Python `hashlib` |
