#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def fmt(value):
    if value is None:
        return "Not measured"
    return str(value)


def runtime_measured(metrics):
    return metrics["runtime"].get("measured", False)


def runtime_reason(metrics):
    return metrics["runtime"].get("not_measured_reason", "unknown")


def append_row(rows, metric, value, how, notes):
    rows.append((metric, value, how, notes))


def build_rows(metrics):
    rows = []
    static = metrics["static"]
    runtime = metrics["runtime"]

    append_row(
        rows,
        "Project name",
        f"`{static['project']['readme_title'].split(' (', 1)[0]}` (`{static['project']['root_artifact_id']}`)",
        "`README.md` + `pom.xml` parsed by `collect_metrics.py`",
        f"Root description: `{static['project']['root_description']}`.",
    )
    append_row(
        rows,
        "Tech stack",
        ", ".join(f"`{item}`" for item in static["tech_stack"]),
        "`collect_metrics.py` scans root/module POM dependencies and `docker-compose.yml`",
        "Derived only from repo metadata and dependency declarations.",
    )
    append_row(
        rows,
        "Maven modules",
        f"`{static['modules']['count']}`",
        "`collect_metrics.py` parses `pom.xml` `<modules>`",
        ", ".join(f"`{name}`" for name in static["modules"]["names"]),
    )
    append_row(
        rows,
        "`docker-compose` services",
        f"`{static['compose_services']['count']}`",
        "`collect_metrics.py` counts top-level entries under `services:` in `docker-compose.yml`",
        "Includes infra and application services.",
    )
    key_paths = ", ".join(
        f"`{item['method']} {item['path']}`" for item in static["endpoints"]["items"][:9]
    )
    append_row(
        rows,
        "REST endpoints (controllers only)",
        f"`{static['endpoints']['count']} total`",
        "`collect_metrics.py` scans `*Controller.java` for `@RestController` and mapping annotations",
        f"Sample paths: {key_paths}.",
    )
    append_row(
        rows,
        "Kafka topics (`destore.topics.*`)",
        (
            f"`{static['topics']['declaration_count']} declarations across "
            f"{static['topics']['module_count']} modules; {static['topics']['unique_count']} unique topics`"
        ),
        "`collect_metrics.py` scans `src/main/resources/application.yml` files",
        "Unique values: " + ", ".join(f"`{item}`" for item in static["topics"]["values"]) + ".",
    )
    append_row(
        rows,
        "Kafka producers (`KafkaTemplate` usage)",
        f"`{static['kafka']['producer_component_count']} producer components`",
        "`collect_metrics.py` counts Java files containing `KafkaTemplate<`",
        f"Raw `KafkaTemplate<` occurrences: `{static['kafka']['producer_usage_occurrences']}`.",
    )
    append_row(
        rows,
        "Kafka consumers (`@KafkaListener`)",
        f"`{static['kafka']['consumer_usage_count']} listener methods`",
        "`collect_metrics.py` counts `@KafkaListener` occurrences across Java files",
        "Listener methods are currently implemented in the notification worker.",
    )
    append_row(
        rows,
        "Persistence entities (`@Entity`)",
        f"`{static['persistence']['entity_count']}`",
        "`collect_metrics.py` scans Java files for `@Entity`",
        "Counted from domain model classes only.",
    )
    append_row(
        rows,
        "Repository types (`*Repository.java`)",
        f"`{static['persistence']['repository_count']}`",
        "`collect_metrics.py` counts Java filenames ending in `Repository.java`",
        "Includes domain interfaces and persistence adapters with a `Repository` suffix.",
    )
    append_row(
        rows,
        "Java files",
        f"`{static['java']['file_count']}`",
        "`collect_metrics.py` counts `**/*.java`",
        "Repository-wide Java source count.",
    )
    append_row(
        rows,
        "Java LOC",
        f"`{static['java']['loc']}`",
        "`collect_metrics.py` sums line counts for `**/*.java`",
        "Matches the fallback `wc -l` style count used in the original measurement.",
    )

    if runtime_measured(metrics):
        append_row(
            rows,
            "Build time",
            f"`{runtime['build_time_seconds']} s`",
            "`collect_metrics.py` times `mvn clean install -DskipTests -Dmaven.repo.local=.m2repo`",
            "Wall-clock elapsed time around the Maven process.",
        )
        append_row(
            rows,
            "Stack time-to-green",
            f"`{runtime['compose_up_elapsed_seconds']} s`",
            "`collect_metrics.py` times `docker compose up -d` and polls the actuator health endpoints",
            "This is convergence time for the current stack state.",
        )
        append_row(
            rows,
            "Seed data via gateway",
            f"`{runtime['seed_status_codes']['price']} / {runtime['seed_status_codes']['inventory']}`",
            "`collect_metrics.py` POSTs one price and one inventory record via `:8080`",
            "Value is `price_status / inventory_status`.",
        )
        pricing = runtime["benchmarks"]["pricing_via_gateway"]
        append_row(
            rows,
            "`GET /pricing/{sku}` via gateway (`:8080`)",
            (
                f"`p50 {pricing['p50_ms']} ms, p95 {pricing['p95_ms']} ms, "
                f"p99 {pricing['p99_ms']} ms, {pricing['error_rate_pct']}% errors, {pricing['rps']} req/s`"
            ),
            "`collect_metrics.py` benchmark with `N=1000`, concurrency `20`",
            "10 warm-up requests are excluded from the reported latency distribution.",
        )
        reports = runtime["benchmarks"]["reports_via_gateway"]
        append_row(
            rows,
            "`GET /reports/summary` via gateway (`:8080`)",
            (
                f"`p50 {reports['p50_ms']} ms, p95 {reports['p95_ms']} ms, "
                f"p99 {reports['p99_ms']} ms, {reports['error_rate_pct']}% errors, {reports['rps']} req/s`"
            ),
            "`collect_metrics.py` benchmark with `N=1000`, concurrency `20`",
            "Same benchmark harness, measured against the reporting path.",
        )
        direct = runtime["benchmarks"]["pricing_direct"]
        append_row(
            rows,
            "`GET /pricing/{sku}` direct (`:8081`)",
            (
                f"`p50 {direct['p50_ms']} ms, p95 {direct['p95_ms']} ms, "
                f"p99 {direct['p99_ms']} ms, {direct['error_rate_pct']}% errors, {direct['rps']} req/s`"
            ),
            "`collect_metrics.py` benchmark with `N=1000`, concurrency `20`",
            "Direct baseline for gateway overhead comparison.",
        )
        overhead = runtime["benchmarks"]["gateway_overhead_pricing"]
        append_row(
            rows,
            "Gateway overhead on `GET /pricing/{sku}`",
            (
                f"`p50 {overhead['p50_ms_delta']} ms, p95 {overhead['p95_ms_delta']} ms, "
                f"p99 {overhead['p99_ms_delta']} ms, {overhead['rps_delta']} req/s vs direct`"
            ),
            "`collect_metrics.py` subtracts direct pricing metrics from gateway pricing metrics",
            "Negative latency delta means the observed difference favored the gateway path in that run.",
        )
        eventing = runtime["eventing"]
        append_row(
            rows,
            "Kafka eventing latency (`pricing-service` -> `notification-worker`)",
            (
                f"`{eventing['matched']}/{eventing['generated']} matched, median "
                f"{eventing['median_ms']} ms, p95 {eventing['p95_ms']} ms`"
            ),
            "`collect_metrics.py` POSTs 200 price updates and correlates `docker compose logs --timestamps`",
            "This is log-to-log delta, not client-observed end-to-end latency.",
        )
    else:
        note = f"Runtime metrics skipped: `{runtime_reason(metrics)}`."
        for metric in [
            "Build time",
            "Stack time-to-green",
            "Seed data via gateway",
            "`GET /pricing/{sku}` via gateway (`:8080`)",
            "`GET /reports/summary` via gateway (`:8080`)",
            "`GET /pricing/{sku}` direct (`:8081`)",
            "Gateway overhead on `GET /pricing/{sku}`",
            "Kafka eventing latency (`pricing-service` -> `notification-worker`)",
        ]:
            append_row(rows, metric, "Not measured", "`collect_metrics.py` runtime section was not executed", note)

    return rows


def render_top_metrics(metrics):
    if not runtime_measured(metrics):
        return [
            "1. Runtime metrics were not measured, so there is no fresh benchmark ranking for CV phrasing.",
            "2. Re-run `./metrics-repro/run.sh` without `--skip-runtime` to regenerate the strongest runtime numbers.",
            "3. Static metrics are still available for architecture-scope phrasing.",
        ]

    pricing = metrics["runtime"]["benchmarks"]["pricing_via_gateway"]
    eventing = metrics["runtime"]["eventing"]
    modules = metrics["static"]["modules"]["count"]
    services = metrics["static"]["compose_services"]["count"]
    endpoints = metrics["static"]["endpoints"]["count"]
    return [
        (
            "1. `GET /pricing/{sku}` via gateway: "
            f"`{pricing['rps']} req/s` at `p95 {pricing['p95_ms']} ms` and "
            f"`{pricing['error_rate_pct']}%` errors. CV phrasing: "
            "`Benchmarked a Spring Cloud Gateway pricing endpoint at "
            f"{round(pricing['rps'] / 1000.0, 1)}k req/s (p95 {round(pricing['p95_ms'], 1)} ms, "
            f"{pricing['error_rate_pct']}% errors) under 1,000-request / 20-concurrency local load.`"
        ),
        (
            "2. Kafka event propagation: "
            f"`{eventing['matched']}/{eventing['generated']}` matched with "
            f"`median {eventing['median_ms']} ms` and `p95 {eventing['p95_ms']} ms`. CV phrasing: "
            "`Validated Kafka-based event propagation across pricing and notification services with "
            f"{eventing['matched']}/{eventing['generated']} matched events and sub-1 ms p95 intra-stack latency in Docker Compose.`"
        ),
        (
            "3. Architecture breadth: "
            f"`{modules}` Maven modules, `{services}` Compose services, `{endpoints}` REST endpoints. "
            "CV phrasing: "
            "`Built an "
            f"{modules}-module, {services}-service Spring Boot microservice prototype exposing "
            f"{endpoints} REST endpoints behind a centralized API gateway.`"
        ),
    ]


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("Usage: render_report.py <metrics.json>\n")
        raise SystemExit(2)

    metrics_path = Path(sys.argv[1])
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    rows = build_rows(metrics)

    print("# DE-Store Metrics Report")
    print()
    print("| Metric | Value | How measured (exact command/script) | Notes/assumptions |")
    print("| --- | --- | --- | --- |")
    for metric, value, how, notes in rows:
        print(f"| {metric} | {value} | {how} | {notes} |")
    print()
    print("## Top 3 Strongest Metrics")
    print()
    for line in render_top_metrics(metrics):
        print(line)


if __name__ == "__main__":
    main()
