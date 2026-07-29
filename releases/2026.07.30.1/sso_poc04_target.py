#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT = "hku-sso-v4"
TARGET_ROOT = Path("/model/dockervolume/hku-custom-sso-v4")
SERVICES = {
    "hku-sso-v4-entrypoint": "host",
    "hku-sso-v4-gateway": "host",
    "hku-sso-v4-session": "host",
    "hku-sso-v4-ui": "host",
    "hku-sso-v4-agent": "host",
    "hku-sso-v4-search": "host",
    "hku-sso-v4-coordinator": "bridge",
    "hku-sso-v4-object": "bridge",
    "hku-sso-v4-vector": "bridge",
}
SEED_CONTAINER = "hku-sso-v4-seed"
TARGET_NAMES = set(SERVICES) | {SEED_CONTAINER}
PORT_OWNERS = {
    "AUTH_ENTRYPOINT_PORT": "hku-sso-v4-entrypoint",
    "AUTH_UI_HOST_PORT": "hku-sso-v4-ui",
    "AUTH_GATEWAY_HOST_PORT": "hku-sso-v4-gateway",
    "AUTH_SESSION_HOST_PORT": "hku-sso-v4-session",
    "NO_SSO_AGENT_HOST_PORT": "hku-sso-v4-agent",
    "NO_SSO_RAG_HOST_PORT": "hku-sso-v4-search",
}
LISTENER_ADDRESSES = {
    "AUTH_ENTRYPOINT_PORT": "0.0.0.0",
    "AUTH_UI_HOST_PORT": "127.0.0.1",
    "AUTH_GATEWAY_HOST_PORT": "127.0.0.1",
    "AUTH_SESSION_HOST_PORT": "127.0.0.1",
    "NO_SSO_AGENT_HOST_PORT": "127.0.0.1",
    "NO_SSO_RAG_HOST_PORT": "127.0.0.1",
}
IMAGE_OWNERS = {
    "hku-sso-v4-entrypoint": "AUTH_PROXY_IMAGE",
    "hku-sso-v4-gateway": "AUTH_GATEWAY_IMAGE",
    "hku-sso-v4-session": "AUTH_SESSION_IMAGE",
    "hku-sso-v4-ui": "NO_SSO_UI_IMAGE",
    "hku-sso-v4-agent": "NO_SSO_AGENT_IMAGE",
    "hku-sso-v4-search": "NO_SSO_RAG_IMAGE",
    "hku-sso-v4-coordinator": "NO_SSO_ETCD_IMAGE",
    "hku-sso-v4-object": "NO_SSO_MINIO_IMAGE",
    "hku-sso-v4-vector": "NO_SSO_MILVUS_IMAGE",
}
VECTOR_ADDRESS_OWNERS = {
    "NO_SSO_VECTOR_COORDINATOR_ADDRESS": "hku-sso-v4-coordinator",
    "NO_SSO_VECTOR_OBJECT_ADDRESS": "hku-sso-v4-object",
    "NO_SSO_VECTOR_ENGINE_ADDRESS": "hku-sso-v4-vector",
}
EXACT_CONTRACT = {
    "AUTH_MODE": "hku",
    "AUTH_PUBLIC_URL": "https://curr-planner.hku.hk",
    "AUTH_EXPECTED_HOST": "curr-planner.hku.hk",
    "AUTH_BACKEND_CERT_IP": "10.64.142.35",
    "AUTH_NETWORK_MODE": "host",
    "AUTH_ENTRYPOINT_PORT": "28380",
    "AUTH_UI_HOST_PORT": "28180",
    "AUTH_GATEWAY_HOST_PORT": "28480",
    "AUTH_SESSION_HOST_PORT": "28679",
    "AUTH_OIDC_ISSUER_URL": "https://oidp.hku.hk/oidc",
    "AUTH_OIDC_SCOPES": "openid hku",
    "AUTH_ALLOWED_EMAIL_DOMAINS": "hku.hk,connect.hku.hk",
    "NO_SSO_TARGET_ROOT": str(TARGET_ROOT),
    "NO_SSO_DATA_ROOT": str(TARGET_ROOT / "data"),
    "NO_SSO_AGENT_HOST_PORT": "28655",
    "NO_SSO_RAG_HOST_PORT": "28891",
    "NO_SSO_COLLECTION": "DATASET_HKU_GB10_POC_04",
    "NO_SSO_EXPECTED_ROWS": "15590",
    "NO_SSO_EXPECTED_LATEST_ROWS": "3785",
    "NO_SSO_EXPECTED_NOT_LATEST_ROWS": "11805",
    "NO_SSO_VECTOR_BACKUP_NAME": "hku_poc04_20260729T000000Z",
    "NO_SSO_VECTOR_BACKUP_SHA256": "d4940dd1121f1022e9394d13a5d595087cf5e88a87ab74281de7cee44445e049",
    "NO_SSO_SEED_CONTAINER": SEED_CONTAINER,
    "NO_SSO_VECTOR_SEED_ROOT": str(TARGET_ROOT / "runtime" / "vector-seed-poc04"),
    "AUTH_AGENT_MODEL": "hku-rag-agent",
    "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
    "DEEPSEEK_MODEL": "deepseek-v4-flash",
    "DEEPSEEK_JUDGE_MODEL": "deepseek-v4-flash",
    "NO_SSO_EMBEDDING_BASE_URL": "http://127.0.0.1:18112",
    "NO_SSO_RERANKER_BASE_URL": "http://127.0.0.1:18113",
    "NO_SSO_UI_SQLITE_BUSY_TIMEOUT_MS": "2000",
    "NO_SSO_UI_MAX_ACTIVE_STREAMS": "256",
    "NO_SSO_UI_MAX_ACTIVE_STREAMS_PER_PRINCIPAL": "1",
    "NO_SSO_UI_MAX_ACTIVE_STREAMS_PER_SESSION": "1",
    "NO_SSO_RAG_MAX_CONCURRENCY": "16",
    "NO_SSO_RAG_MAX_QUEUE": "16",
    "NO_SSO_RAG_QUEUE_TIMEOUT_SECONDS": "5",
    "NO_SSO_CONTROL_URLS": (
        "http://10.64.142.35:18380/healthz,"
        "http://10.64.142.35:20380/healthz,"
        "http://127.0.0.1:18890/health,"
        "https://127.0.0.1:443/__health"
    ),
    "NO_SSO_CONTROL_PORTS": "443,18380,20380,18890",
}
IMAGE_KEYS = tuple(dict.fromkeys((*IMAGE_OWNERS.values(), "NO_SSO_VECTOR_SEED_IMAGE")))
SECRET_ENV_KEYS = (
    "AUTH_OIDC_CLIENT_ID",
    "AUTH_AGENT_API_KEY",
    "RAG_API_KEY",
    "DEEPSEEK_API_KEY",
    "NO_SSO_MINIO_ROOT_PASSWORD",
)
PLACEHOLDERS = ("REPLACE_WITH", "CHANGEME", "EXAMPLE", "DEFAULT_VALUE")


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def run(*command: str, check: bool = True) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        fail(f"{' '.join(command[:3])} failed: {detail}")
    return result.stdout


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        fail(f"environment file is missing: {path}")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            fail(f"invalid environment entry at {path}:{number}")
        key, value = line.split("=", 1)
        key = key.strip()
        if key in values:
            fail(f"duplicate environment key: {key}")
        values[key] = value.strip()
    return values


def require(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        fail(f"{key} is required")
    return value


def update_env(path: Path, replacements: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        key, separator, _ = line.partition("=")
        if separator and key in replacements:
            output.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key in sorted(set(replacements) - seen):
        output.append(f"{key}={replacements[key]}")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        stream.write("\n".join(output) + "\n")
        temporary = Path(stream.name)
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)


def validate_contract(values: dict[str, str], root: Path) -> None:
    if root.resolve() != TARGET_ROOT:
        fail(f"target root must be {TARGET_ROOT}")
    if root.is_symlink():
        fail("target root must not be a symbolic link")
    for key, expected in EXACT_CONTRACT.items():
        if require(values, key) != expected:
            fail(f"{key} must be {expected}")
    owner = require(values, "AUTH_TLS_OWNER_UID")
    if not owner.isdigit() or int(owner) != os.getuid():
        fail("AUTH_TLS_OWNER_UID must match the deployment uid")
    ports = [int(require(values, key)) for key in PORT_OWNERS]
    if len(ports) != len(set(ports)):
        fail("target host ports must be unique")
    if any(port < 1024 or port > 65535 for port in ports):
        fail("target services must use unprivileged host ports")
    domains = require(values, "AUTH_ALLOWED_EMAIL_DOMAINS").split(",")
    if domains != ["hku.hk", "connect.hku.hk"]:
        fail("the qualified staff and student email-domain allowlist changed")
    for key in SECRET_ENV_KEYS:
        value = require(values, key)
        if any(marker in value.upper() for marker in PLACEHOLDERS):
            fail(f"{key} still contains a placeholder")
    for key in ("AUTH_AGENT_API_KEY", "RAG_API_KEY", "NO_SSO_MINIO_ROOT_PASSWORD"):
        if len(require(values, key)) < 48:
            fail(f"{key} is too short")
    for key in IMAGE_KEYS:
        reference = require(values, key)
        if "@sha256:" not in reference:
            fail(f"{key} must use an immutable registry digest")
    for key in VECTOR_ADDRESS_OWNERS:
        value = require(values, key)
        if value == "AUTO":
            continue
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            fail(f"{key} must be AUTO or a private IPv4 address")
        if (
            not isinstance(address, ipaddress.IPv4Address)
            or not address.is_private
            or address.is_loopback
        ):
            fail(f"{key} must be AUTO or a private non-loopback IPv4 address")


def inspect_raw(name: str) -> dict[str, Any] | None:
    output = run("docker", "inspect", name, check=False).strip()
    if not output:
        return None
    items = json.loads(output)
    return items[0] if isinstance(items, list) and len(items) == 1 else None


def normalized_container(name: str) -> dict[str, Any] | None:
    item = inspect_raw(name)
    if item is None:
        return None
    state = item.get("State") or {}
    config = item.get("Config") or {}
    labels = config.get("Labels") or {}
    return {
        "id": item.get("Id", ""),
        "image_id": item.get("Image", ""),
        "state": state.get("Status", ""),
        "health": (state.get("Health") or {}).get("Status", "none"),
        "started_at": state.get("StartedAt", ""),
        "restart_count": item.get("RestartCount", 0),
        "project": labels.get("com.docker.compose.project", ""),
        "network_mode": (item.get("HostConfig") or {}).get("NetworkMode", ""),
        "pid_mode": (item.get("HostConfig") or {}).get("PidMode", ""),
        "port_bindings": (item.get("HostConfig") or {}).get("PortBindings") or {},
        "mounts": sorted(
            (
                {
                    "source": mount.get("Source", ""),
                    "destination": mount.get("Destination", ""),
                    "read_write": bool(mount.get("RW")),
                }
                for mount in item.get("Mounts") or []
            ),
            key=lambda value: (value["destination"], value["source"]),
        ),
    }


def existing_controls() -> dict[str, Any]:
    names = run("docker", "ps", "-a", "--format", "{{.Names}}").splitlines()
    return {
        name: inspected
        for name in sorted(names)
        if name not in TARGET_NAMES and (inspected := normalized_container(name)) is not None
    }


def health(url: str) -> dict[str, Any]:
    context = None
    request: str | urllib.request.Request = url
    if url.startswith("https://"):
        # The existing control lane intentionally uses a self-signed backend cert.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if url == "https://127.0.0.1:443/__health":
        request = urllib.request.Request(
            url,
            headers={"Host": "curr-planner.hku.hk"},
        )
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            return {"status": response.status, "bytes": len(response.read())}
    except (urllib.error.URLError, TimeoutError) as error:
        fail(f"control endpoint is unavailable: {url}: {error}")


def control_urls(values: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        value.strip()
        for value in require(values, "NO_SSO_CONTROL_URLS").split(",")
        if value.strip()
    )


def control_ports(values: dict[str, str]) -> tuple[int, ...]:
    ports: list[int] = []
    for raw in require(values, "NO_SSO_CONTROL_PORTS").split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            port = int(value)
        except ValueError:
            fail(f"invalid control port: {value}")
        if not 1 <= port <= 65535:
            fail(f"control port is out of range: {port}")
        ports.append(port)
    if len(ports) != len(set(ports)):
        fail("control port list contains duplicates")
    return tuple(ports)


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def listener_addresses(port: int) -> set[str]:
    output = run("ss", "-H", "-ltn")
    addresses: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        address, candidate = fields[3].rsplit(":", 1)
        if candidate == str(port):
            addresses.add(address.strip("[]"))
    return addresses


def image_id(reference: str) -> str:
    return run("docker", "image", "inspect", reference, "--format", "{{.Id}}", check=False).strip()


def image_platform(reference: str) -> str:
    return run(
        "docker",
        "image",
        "inspect",
        reference,
        "--format",
        "{{.Os}}/{{.Architecture}}",
        check=False,
    ).strip()


def has_published_ports(item: dict[str, Any]) -> bool:
    return bool((item.get("HostConfig") or {}).get("PortBindings") or {})


def container_ipv4(item: dict[str, Any], name: str) -> str:
    networks = (item.get("NetworkSettings") or {}).get("Networks") or {}
    addresses = sorted(
        value.get("IPAddress", "") for value in networks.values() if value.get("IPAddress")
    )
    if len(addresses) != 1:
        fail(f"expected one private bridge address for {name}; found {addresses}")
    address = ipaddress.ip_address(addresses[0])
    if (
        not isinstance(address, ipaddress.IPv4Address)
        or not address.is_private
        or address.is_loopback
    ):
        fail(f"invalid private bridge address for {name}: {address}")
    return str(address)


def validate_discovery(values: dict[str, str]) -> None:
    issuer = require(values, "AUTH_OIDC_ISSUER_URL").rstrip("/")
    request = urllib.request.Request(
        issuer + "/.well-known/openid-configuration",
        headers={"User-Agent": "hku-deployment-check/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            metadata = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        fail(f"OIDC discovery is unavailable or invalid: {error}")
    if metadata.get("issuer") != issuer:
        fail("OIDC discovery issuer does not match the configured issuer")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not str(metadata.get(key, "")).startswith("https://"):
            fail(f"OIDC discovery has no secure {key}")


def preflight(root: Path, env_path: Path) -> None:
    values = read_env(env_path)
    validate_contract(values, root)
    validate_discovery(values)
    for key in IMAGE_KEYS:
        reference = require(values, key)
        if not image_id(reference):
            fail(f"required image is not present: {reference}")
        if image_platform(reference) != "linux/arm64":
            fail(f"required image is not Linux ARM64: {reference}")
    for name, mode in SERVICES.items():
        item = inspect_raw(name)
        if item is None:
            continue
        labels = (item.get("Config") or {}).get("Labels") or {}
        actual_mode = (item.get("HostConfig") or {}).get("NetworkMode", "")
        if labels.get("com.docker.compose.project") != PROJECT:
            fail(f"target container name belongs to another project: {name}")
        if mode == "host" and actual_mode != "host":
            fail(f"existing target container has the wrong network mode: {name}")
        if mode == "bridge" and actual_mode not in {"bridge", "default"}:
            fail(f"existing vector container has the wrong network mode: {name}")
        if mode == "bridge" and has_published_ports(item):
            fail(f"private vector dependency publishes a host port: {name}")
    seed = inspect_raw(SEED_CONTAINER)
    if seed is not None and seed.get("Image") != image_id(require(values, "NO_SSO_VECTOR_SEED_IMAGE")):
        fail("existing seed container does not match the release image")
    for key, owner in PORT_OWNERS.items():
        port = int(require(values, key))
        if port_is_free(port):
            continue
        item = normalized_container(owner)
        if item is None or item["project"] != PROJECT or item["state"] != "running":
            fail(f"target port is occupied outside {PROJECT}: {port}")
        if item["network_mode"] != "host":
            fail(f"cannot prove host listener ownership for {owner}")
        if listener_addresses(port) != {LISTENER_ADDRESSES[key]}:
            fail(f"target port has an unexpected bind address: {port}")
    for url in control_urls(values):
        if health(url)["status"] != 200:
            fail(f"control endpoint did not return 200: {url}")
    for port in control_ports(values):
        if not listener_addresses(port):
            fail(f"control listener is unavailable: {port}")
    print("SSO POC04 target preflight ok")


def sync_vector_addresses(env_path: Path) -> None:
    replacements: dict[str, str] = {}
    for key, name in VECTOR_ADDRESS_OWNERS.items():
        item = inspect_raw(name)
        if item is None:
            fail(f"private vector dependency is missing: {name}")
        labels = (item.get("Config") or {}).get("Labels") or {}
        host = item.get("HostConfig") or {}
        if labels.get("com.docker.compose.project") != PROJECT:
            fail(f"private vector dependency belongs to another project: {name}")
        if (item.get("State") or {}).get("Status") != "running":
            fail(f"private vector dependency is not running: {name}")
        if host.get("NetworkMode") not in {"bridge", "default"} or has_published_ports(item):
            fail(f"private vector boundary changed: {name}")
        replacements[key] = container_ipv4(item, name)
    if len(set(replacements.values())) != len(replacements):
        fail("private vector dependencies do not have unique addresses")
    update_env(env_path, replacements)
    print("private vector dependency addresses synchronized")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(payload, stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def capture(path: Path, env_path: Path) -> None:
    values = read_env(env_path)
    write_json(
        path,
        {
            "schema_version": 1,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "containers": existing_controls(),
            "health": {url: health(url) for url in control_urls(values)},
            "listeners": {
                str(port): sorted(listener_addresses(port))
                for port in control_ports(values)
            },
        },
    )
    print(f"control snapshot written: {path}")


def compare(path: Path, env_path: Path) -> None:
    before = json.loads(path.read_text(encoding="utf-8"))
    after = existing_controls()
    errors: list[str] = []
    for name, expected in before["containers"].items():
        actual = after.get(name)
        if actual is None:
            errors.append(f"existing container disappeared: {name}")
        elif actual != expected:
            errors.append(f"existing container changed: {name}")
    for url in control_urls(read_env(env_path)):
        if health(url)["status"] != 200:
            errors.append(f"control endpoint is unhealthy: {url}")
    values = read_env(env_path)
    expected_listeners = before.get("listeners", {})
    for port in control_ports(values):
        expected = expected_listeners.get(str(port))
        actual = sorted(listener_addresses(port))
        if expected is None:
            errors.append(f"control listener was not captured: {port}")
        elif actual != expected:
            errors.append(f"control listener changed: {port}")
    if errors:
        fail("; ".join(errors))
    print("all pre-existing containers and control endpoints are unchanged")


def verify_target(root: Path, env_path: Path) -> None:
    values = read_env(env_path)
    validate_contract(values, root)
    errors: list[str] = []
    for name, mode in SERVICES.items():
        item = inspect_raw(name)
        if item is None:
            errors.append(f"target container is missing: {name}")
            continue
        state = item.get("State") or {}
        labels = (item.get("Config") or {}).get("Labels") or {}
        host = item.get("HostConfig") or {}
        expected_image = image_id(require(values, IMAGE_OWNERS[name]))
        if state.get("Status") != "running":
            errors.append(f"target container is not running: {name}")
        if (state.get("Health") or {}).get("Status", "none") not in {"healthy", "none"}:
            errors.append(f"target container health failed: {name}")
        if labels.get("com.docker.compose.project") != PROJECT:
            errors.append(f"target project label changed: {name}")
        if not expected_image or item.get("Image") != expected_image:
            errors.append(f"target image identity changed: {name}")
        if host.get("RestartPolicy", {}).get("Name") != "unless-stopped":
            errors.append(f"restart policy changed: {name}")
        actual_mode = host.get("NetworkMode", "")
        if mode == "host" and actual_mode != "host":
            errors.append(f"host-network boundary changed: {name}")
        if mode == "bridge" and actual_mode not in {"bridge", "default"}:
            errors.append(f"default-bridge boundary changed: {name}")
        if mode == "bridge" and has_published_ports(item):
            errors.append(f"private vector dependency publishes a host port: {name}")
        for mount in item.get("Mounts") or []:
            source = Path(mount.get("Source", "")).resolve()
            try:
                source.relative_to(root.resolve())
            except ValueError:
                errors.append(f"mount escapes the target root: {name}: {source}")
        if mode == "bridge":
            key = next(key for key, owner in VECTOR_ADDRESS_OWNERS.items() if owner == name)
            if require(values, key) != container_ipv4(item, name):
                errors.append(f"private vector address changed: {name}")
    for key in PORT_OWNERS:
        port = int(require(values, key))
        if listener_addresses(port) != {LISTENER_ADDRESSES[key]}:
            errors.append(f"listener address changed for port {port}")
    if errors:
        fail("; ".join(errors))
    print("SSO POC04 target ownership, persistence, and network boundaries verified")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--env", type=Path, default=Path.cwd() / ".env")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config")
    commands.add_parser("preflight")
    commands.add_parser("sync-vector-addresses")
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--output", type=Path, required=True)
    compare_parser = commands.add_parser("compare")
    compare_parser.add_argument("--before", type=Path, required=True)
    commands.add_parser("verify-target")
    args = parser.parse_args()
    if args.command == "check-config":
        validate_contract(read_env(args.env), args.root)
        print("SSO POC04 target configuration ok")
    elif args.command == "preflight":
        preflight(args.root, args.env)
    elif args.command == "sync-vector-addresses":
        sync_vector_addresses(args.env)
    elif args.command == "capture":
        capture(args.output, args.env)
    elif args.command == "compare":
        compare(args.before, args.env)
    else:
        verify_target(args.root, args.env)


if __name__ == "__main__":
    main()
