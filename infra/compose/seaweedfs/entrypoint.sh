#!/bin/sh
# Renders the S3 identity config from env vars (no credentials baked into files),
# then starts a single-node SeaweedFS (master + volume + filer + S3 gateway).
set -eu
: "${S3_ACCESS_KEY_ID:?S3_ACCESS_KEY_ID is required}"
: "${S3_SECRET_ACCESS_KEY:?S3_SECRET_ACCESS_KEY is required}"
cat > /tmp/s3.json <<JSON
{
  "identities": [
    {
      "name": "vip-dev",
      "credentials": [{ "accessKey": "${S3_ACCESS_KEY_ID}", "secretKey": "${S3_SECRET_ACCESS_KEY}" }],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
JSON
# Explicit limits: the default (-volume.max=0) sizes capacity from free disk at boot, which
# left zero writable volumes when Docker's disk was full. 1 GB volumes x 100 = up to 100 GB.
exec weed server -dir=/data -ip.bind=0.0.0.0 \
  -master.volumeSizeLimitMB=1024 -volume.max=100 \
  -s3 -s3.port=8333 -s3.config=/tmp/s3.json
