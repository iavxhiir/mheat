# MHEAT Helm chart

Mediterranean Marine Heatwave Dashboard for EDITO. Two operating modes:

| Mode | When | What |
|---|---|---|
| **Demo** *(default)* | EDITO judging path, smoke tests, anyone without CMS creds | Bundled sample SST cube, no Copernicus calls. Zero-config. |
| **Live** | Operational deployment against Copernicus Marine | Pulls NRT/forecast SST, broadcasts a pre-computed Hobday climatology, refreshes daily via the CronJob. |

## Demo install (no creds, no PVC bootstrap)

```bash
helm upgrade --install mheat ./charts/mheat \
  --set ingress.hosts[0].host=mheat.edito.example
```

The Deployment comes up ready in ~10 s, serving the embedded fixture cube.

## Live mode

Live mode requires (a) Copernicus Marine credentials and (b) a pre-computed
Hobday per-DOY climatology zarr in a PVC. The chart provisions both, then
wires the Deployment to use them.

### Step 1 — inject CMS credentials

```bash
kubectl create secret generic mheat-cms \
  --from-literal=username="$COPERNICUSMARINE_SERVICE_USERNAME" \
  --from-literal=password="$COPERNICUSMARINE_SERVICE_PASSWORD"
```

The chart references this secret via `envFrom` so the credentials never
appear in `helm get values` / Deployment spec / ConfigMaps.

### Step 2 — bootstrap the climatology

```bash
helm upgrade --install mheat ./charts/mheat \
  --set live.enabled=true \
  --set live.bootstrap.enabled=true \
  --set ingress.hosts[0].host=mheat.edito.example
```

This applies a one-shot `Job` that runs `python scripts/bootstrap_climatology.py`
inside the main image, downloads the Mediterranean reanalysis (~10–30 GB
egress, hours, charged against the operator's CMS quota), and writes the
~500 MB climatology zarr to the `<release>-climatology` PVC.

The Deployment will start immediately but its readiness probe (`/api/readyz`)
will report `climatology_present: false` until the Job finishes, so it
remains out of rotation behind the Service.

Watch the Job:

```bash
kubectl logs -f job/mheat-bootstrap-climatology
```

### Step 3 — turn off the bootstrap toggle

After the Job has succeeded, re-apply the chart with bootstrap disabled so
subsequent `helm upgrade` calls don't try to re-run it (idempotency
short-circuits on `.zmetadata` anyway, but it's cleaner to remove the
manifest):

```bash
helm upgrade mheat ./charts/mheat \
  --set live.enabled=true \
  --set live.bootstrap.enabled=false \
  --set ingress.hosts[0].host=mheat.edito.example
```

The Deployment will become ready as soon as it sees the populated zarr
through its read-only PVC mount.

### Optional — daily NRT updates

Keep the Zarr cube fresh by enabling the daily-update CronJob:

```bash
helm upgrade mheat ./charts/mheat \
  --set live.enabled=true \
  --set dailyUpdate.enabled=true
```

It runs `scripts/update_daily.py` against the same shared cache PVC.

## Readiness gating

`/api/readyz` checks two things against the live runtime:

* `cache_dir` is writable (the cache PVC is mounted), **and**
* `climatology_present` — `True` only when `CLIMATOLOGY_STORE` resolves to
  an existing path, which after Step 2 is the read-only PVC mount.

That second flag is the gate that keeps a half-bootstrapped install out of
service rotation.

## Multi-replica notes

`live.climatology.pvc.accessMode` defaults to `ReadOnlyMany` because the
zarr is immutable after bootstrap and we want every Deployment replica to
mount the same volume. Most cloud block-storage classes (AWS EBS, Azure
Disk, GCE PD) only support `ReadWriteOnce` — in that case either:

* keep `replicaCount: 1` (single-node read), or
* provision an RWX-capable StorageClass (NFS, EFS, CephFS, Longhorn) and
  set `live.climatology.pvc.storageClass` accordingly.

The bootstrap Job itself mounts the PVC RW (one-Pod RW is compatible with
either access mode), so a single bootstrap → many readers is the supported
topology.

## Values reference

See [`values.yaml`](values.yaml) — every key has an inline comment.
The live-mode block is grouped under `live.*`.
