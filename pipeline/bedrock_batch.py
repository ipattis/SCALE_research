"""Minimal Bedrock batch-inference runner (~50% cheaper than on-demand).

    results = run_batch("enrich-2024", FAST_MODEL, {record_id: messages_body, ...})

Uploads a JSONL to S3, submits a model-invocation job, polls until it finishes and returns
{record_id: response_body}. Job state is saved in data/batch_jobs.json, so if the script is
interrupted, re-running it re-attaches to the same job instead of paying twice.

Bedrock requires at least MIN_RECORDS per job; smaller workloads fall back to on-demand calls.
Infra (created once, shared with the NeurIPS map project): S3 bucket neurips-map-batch-<account> and
IAM role neurips-map-bedrock-batch (S3 access to that bucket + bedrock:InvokeModel). Objects for this
project go under the scale-map/ prefix.
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import boto3

from common import AWS_REGION, DATA, llm_client

MIN_RECORDS = 100
STATE = DATA / "batch_jobs.json"
TERMINAL = {"Completed", "PartiallyCompleted", "Failed", "Stopped", "Expired"}


def _infra():
    account = boto3.client("sts").get_caller_identity()["Account"]
    bucket = os.environ.get("SCALE_BATCH_BUCKET", f"neurips-map-batch-{account}")
    role = os.environ.get("SCALE_BATCH_ROLE", f"arn:aws:iam::{account}:role/neurips-map-bedrock-batch")
    return bucket, role


def _load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _save_state(state):
    STATE.write_text(json.dumps(state, indent=2))


def _on_demand(model, requests, workers=12):
    client = llm_client()

    def one(item):
        rid, body = item
        try:
            resp = client.messages.create(model=model, **body)
            return rid, resp.model_dump()
        except Exception as e:  # mirror batch semantics: failures are reported, not raised
            return rid, {"error": repr(e)[:300]}

    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, requests.items()))


def run_batch(job_key, model, requests, poll_seconds=60):
    """Run requests ({record_id: Messages API body without 'model'}) and return {record_id: response}.

    A response is the Messages API response dict, or {"error": ...} for a failed record.
    """
    if not requests:
        return {}
    if len(requests) < MIN_RECORDS:
        print(f"[{job_key}] {len(requests)} records < {MIN_RECORDS}; using on-demand calls")
        return _on_demand(model, requests)

    bedrock = boto3.client("bedrock", region_name=AWS_REGION)
    s3 = boto3.client("s3", region_name=AWS_REGION)
    bucket, role = _infra()
    state = _load_state()
    job = state.get(job_key)

    # Reuse an existing job only if it was for exactly the same set of records.
    if job and sorted(job["record_ids"]) != sorted(requests):
        print(f"[{job_key}] records changed since last submission; submitting a new job")
        job = None
    if job:
        status = bedrock.get_model_invocation_job(jobIdentifier=job["arn"])["status"]
        if status in {"Failed", "Stopped", "Expired"}:
            print(f"[{job_key}] previous job {status}; resubmitting")
            job = None

    if not job:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        prefix = f"scale-map/{job_key}/{stamp}"
        lines = [
            json.dumps({"recordId": rid, "modelInput": {"anthropic_version": "bedrock-2023-05-31", **body}})
            for rid, body in requests.items()
        ]
        s3.put_object(Bucket=bucket, Key=f"{prefix}/input.jsonl", Body="\n".join(lines).encode())
        try:
            resp = bedrock.create_model_invocation_job(
                jobName=f"{job_key}-{stamp}"[:63],
                roleArn=role,
                modelId=model,
                inputDataConfig={"s3InputDataConfig": {"s3Uri": f"s3://{bucket}/{prefix}/input.jsonl"}},
                outputDataConfig={"s3OutputDataConfig": {"s3Uri": f"s3://{bucket}/{prefix}/output/"}},
                timeoutDurationInHours=24,
            )
        except bedrock.exceptions.ValidationException as e:
            if "not supported for the requested model" not in str(e):
                raise
            print(f"[{job_key}] WARNING: {model} doesn't support batch inference; using on-demand calls")
            return _on_demand(model, requests)
        job = {"arn": resp["jobArn"], "prefix": prefix, "record_ids": list(requests)}
        state[job_key] = job
        _save_state(state)
        print(f"[{job_key}] submitted {len(requests)} records as {resp['jobArn'].split('/')[-1]}")

    start = time.time()
    while True:
        info = bedrock.get_model_invocation_job(jobIdentifier=job["arn"])
        status = info["status"]
        if status in TERMINAL:
            break
        print(f"[{job_key}] {status} ({(time.time() - start) / 60:.0f} min)", flush=True)
        time.sleep(poll_seconds)
    if status not in {"Completed", "PartiallyCompleted"}:
        raise RuntimeError(f"[{job_key}] batch job {status}: {info.get('message')}")

    job_id = job["arn"].split("/")[-1]
    out_key = f"{job['prefix']}/output/{job_id}/input.jsonl.out"
    body = s3.get_object(Bucket=bucket, Key=out_key)["Body"].read().decode()
    results = {}
    for line in body.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        results[rec["recordId"]] = rec.get("modelOutput") or {"error": rec.get("error", "missing output")}
    n_err = sum("error" in r for r in results.values())
    print(f"[{job_key}] {status}: {len(results) - n_err} ok, {n_err} errors, {len(requests) - len(results)} missing")
    return results
