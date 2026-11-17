# LocalFlow data-contribution server

Collects recordings and typed text from people who have **opted in** ("Help improve LocalFlow" in the Android app),
so they can fine-tune LocalFlow's speech models later. Standard library Python only; runs on k3s.

Nothing here collects anything silently: the app uploads nothing until the person turns the option on and accepts the
consent text, and "Delete my data" in the app removes everything from this server immediately.

## What is stored

Per sample: the audio (16 kHz mono WAV), the text the speech model heard, the text the app typed, the language mode,
model names, app version, device tier and a coarse device name. **Not stored:** IP addresses (no access log), names, phone
numbers, e-mail, account ids, contacts, anything from other apps, or audio from password fields.
An install is just a random id plus a hashed random token.

## Live deployment (partharsid.dev cluster)

Deployed through the cluster's GitOps repo: `k3s-gitops/apps/localflow` (an Argo CD Application, namespace `localflow`,
a stock `python:3.12-slim` pod with `ingest.py` mounted from a ConfigMap, 20 Gi local-path volume). It is published as
**https://flow-data.partharsid.dev** by a route on the `k3s` Cloudflare tunnel to
`http://localflow-ingest.localflow.svc.cluster.local:80`. To change the server, edit `server/ingest.py`, copy it to
`k3s-gitops/apps/localflow/ingest.py` and push: Argo CD rolls the pod.

Build the app against it: `gradle :app:assembleRelease -Plf.uploadUrl=https://flow-data.partharsid.dev`.

## Deploy on k3s (generic, without the GitOps repo)

```bash
# 1. Build the image on a machine with Docker, then load it into k3s (or push to your registry and edit the image name)
docker build -t localflow-ingest:1 server/
docker save localflow-ingest:1 | ssh <node> 'sudo k3s ctr images import -'

# 2. Set your domain in k8s/30-ingress.yaml (and your cert-manager ClusterIssuer), then
kubectl apply -f server/k8s/

# 3. Check
curl https://<your-domain>/healthz        # {"ok": true}
```

Then build the Android app pointing at it:

```bash
gradle :app:assembleRelease -Plf.uploadUrl=https://<your-domain>
```

Without `lf.uploadUrl` the "Help improve LocalFlow" option is hidden, so a build can never upload anywhere by accident.

## Data and backups

Data lives on the `localflow-ingest-data` volume: `audio/<xx>/<install>/<sample>.wav` and `meta.db` (SQLite).
With k3s' local-path storage that is a directory on the node (`/var/lib/rancher/k3s/storage/...`): back it up with
restic/rsync. The audio is personal voice data: keep the node disk encrypted and restrict who can read the volume.

## Using the data

```bash
kubectl -n localflow exec deploy/localflow-ingest -- python export_dataset.py --data /data --out /data/dataset.jsonl
kubectl -n localflow cp localflow/<pod>:/data/dataset.jsonl dataset.jsonl     # plus the audio folder
```

`raw_text`/`final_text` are not human-verified transcripts. Re-label first (for example with the larger
Whisper-Hindi2Hinglish-Apex model, keeping clips where it agrees with `raw_text`), then fine-tune.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | liveness |
| POST | `/v1/register` | body `{"consent_version":"1"}` returns `{install_id, token}` |
| POST | `/v1/samples` | `Authorization: Bearer <token>`, header `X-Meta` (JSON), body = WAV |
| GET | `/v1/me` | how many samples this install has stored |
| DELETE | `/v1/me` | deletes this install's audio, metadata and token |

Limits: 4 MB / 70 s per sample, 400 samples per install per day, 60 registrations per hour for the whole server.

## Tests

`cd server && python -m unittest test_ingest`
