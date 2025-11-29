# Metrics Repro Bundle

This folder contains the code and commands used to produce the DE-Store metrics report.

## Contents

- `collect_metrics.py`: computes static metrics from the repo and runtime metrics from Maven, Docker Compose, HTTP calls, and Docker logs.
- `render_report.py`: converts the JSON output into the same Markdown report format.
- `run.sh`: runs both scripts and writes fresh output files into `metrics-repro/output/`.
- `.gitignore`: ignores generated output files.

## Prerequisites

- `python3`
- `mvn`
- For runtime metrics: `docker` with Compose plugin and permission to talk to the Docker daemon

The scripts use only Python's standard library.

## Exact Commands

Static metrics only:

```bash
python3 metrics-repro/collect_metrics.py --repo-root . --skip-runtime --output-json metrics-repro/output/metrics.json
python3 metrics-repro/render_report.py metrics-repro/output/metrics.json > metrics-repro/output/metrics-report.md
```

Static + runtime metrics:

```bash
python3 metrics-repro/collect_metrics.py --repo-root . --output-json metrics-repro/output/metrics.json
python3 metrics-repro/render_report.py metrics-repro/output/metrics.json > metrics-repro/output/metrics-report.md
```

One-shot wrapper:

```bash
./metrics-repro/run.sh
./metrics-repro/run.sh --skip-runtime
```

## External Commands Invoked By `collect_metrics.py`

- `mvn clean install -DskipTests -Dmaven.repo.local=.m2repo`
- `docker ps`
- `docker compose up -d`
- `docker compose logs --since ... --timestamps pricing-service`
- `docker compose logs --since ... --timestamps notification-worker`

## Runtime Measurement Notes

- Build timing uses wall-clock elapsed time around the Maven command.
- Time-to-green measures `docker compose up -d` convergence and then polls:
  - `http://localhost:8080/actuator/health`
  - `http://localhost:8081/actuator/health`
  - `http://localhost:8082/actuator/health`
  - `http://localhost:8083/actuator/health`
  - `http://localhost:8085/actuator/health`
  - `http://localhost:8086/actuator/health`
- The benchmark uses Python stdlib HTTP clients with `N=1000`, concurrency `20`, and a 10-request warm-up.
- Event latency is computed by correlating Docker log timestamps between:
  - `Price set for sku=...`
  - `price changed for ...`

## Output Files

`run.sh` writes:

- `metrics-repro/output/metrics.json`
- `metrics-repro/output/metrics-report.md`

## Reproducing The Existing Report

The current repo-level report in `destore-metrics-report.md` was produced from the same measurement approach captured in this folder. Re-running the scripts may produce different runtime values if the stack state, machine load, caches, or Docker image state have changed.
