"""
Vaultwarden 构件获取模块

二进制的正确来源（重要）：
  官方 vaultwarden/server 镜像分两种，都不能直接用于群晖：
    1. 标准镜像 (Debian) -> 动态链接 glibc，需要 /lib64/ld-linux-x86-64.so.2
       群晖 DSM 没有配套 glibc 加载器，装上去跑不起来。
    2. -alpine 镜像      -> 静态 musl，能跑；但按较新 CPU 基线编译，
       老机型（Apollo Lake / Gemini Lake / Atom）缺 SSE4.2 / AVX，新版本
       二进制直接 SIGILL 崩溃。

  实测证据（2026-11-02）：
    1.37.4-alpine 二进制 sha256=392bc9af... 在用户 NAS 正常
    1.37.5-alpine 二进制 sha256=cdf139bd... 替换进 1.37.4 后套件起不来

  因此 x86_64 从源码自编（-C target-cpu=x86-64，不含 SSE4.2/AVX），
  静态 musl。ARM64（Cortex-A53）本就是 ARMv8 基线，无此问题，继续用镜像。

web-vault 是纯静态前端（JS/CSS/HTML），不含本地代码、不依赖 CPU 指令集，
直接从官方镜像提取即可。
"""

import os
import io
import json
import gzip
import shutil
import tarfile
import subprocess
import urllib.request
from pathlib import Path

REPO = "vaultwarden/server"


def _docker_ok() -> bool:
    try:
        return subprocess.run(
            ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode == 0
    except Exception:
        return False


def _registry_token() -> str:
    url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{REPO}:pull"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=30
    ) as r:
        return json.loads(r.read())["token"]


def _arch_manifest_digest(tag: str, docker_arch: str, token: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Authorization": f"Bearer {token}",
        "Accept": (
            "application/vnd.docker.distribution.manifest.list.v2+json, "
            "application/vnd.oci.image.index.v1+json"
        ),
    }
    with urllib.request.urlopen(
        urllib.request.Request(f"https://registry-1.docker.io/v2/{REPO}/manifests/{tag}", headers=headers),
        timeout=30,
    ) as resp:
        idx = json.loads(resp.read())

    for m in idx.get("manifests", []):
        p = m.get("platform", {})
        if p.get("os") == "linux" and p.get("architecture") == docker_arch:
            return m["digest"]
    raise RuntimeError(f"镜像 {tag} 中没有 {docker_arch} 架构")


def _image_manifest(digest: str, token: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Authorization": f"Bearer {token}",
        "Accept": (
            "application/vnd.docker.distribution.manifest.v2+json, "
            "application/vnd.oci.image.manifest.v1+json"
        ),
    }
    with urllib.request.urlopen(
        urllib.request.Request(f"https://registry-1.docker.io/v2/{REPO}/manifests/{digest}", headers=headers),
        timeout=30,
    ) as resp:
        return json.loads(resp.read())


def _iter_layer_tars(manifest: dict, token: str):
    """逐层产出已解压的 tarfile 对象（调用方需自行 close）。"""
    for layer in manifest.get("layers", []):
        ldig = layer["digest"]
        req = urllib.request.Request(
            f"https://registry-1.docker.io/v2/{REPO}/blobs/{ldig}",
            headers={"User-Agent": "Mozilla/5.0", "Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=180) as resp:
            blob = resp.read()
        try:
            raw = gzip.decompress(blob)
        except Exception:
            continue
        try:
            t = tarfile.open(fileobj=io.BytesIO(raw), mode="r:")
        except Exception:
            continue
        yield t
        t.close()


def fetch_binary_from_image(version: str, docker_arch: str) -> bytes:
    """
    从官方 -alpine 镜像提取二进制（仅用于 ARM64；x86_64 走源码自编）。
    """
    tag = f"{version}-alpine"

    if _docker_ok():
        cname = f"vw_bin_{docker_arch}_{os.getpid()}"
        platform = f"linux/{docker_arch}"
        subprocess.run(
            ["docker", "pull", "--platform", platform, f"{REPO}:{tag}"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            subprocess.run(
                ["docker", "create", "--name", cname, "--platform", platform, f"{REPO}:{tag}"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"{cname}.bin"
            subprocess.run(
                ["docker", "cp", f"{cname}:/vaultwarden", str(tmp)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            data = tmp.read_bytes()
            tmp.unlink(missing_ok=True)
            return data
        finally:
            subprocess.run(
                ["docker", "rm", "-f", cname],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    token = _registry_token()
    digest = _arch_manifest_digest(tag, docker_arch, token)
    manifest = _image_manifest(digest, token)
    for t in _iter_layer_tars(manifest, token):
        for m in t.getmembers():
            if m.name.lstrip("./") == "vaultwarden" and m.isfile():
                return t.extractfile(m).read()
    raise FileNotFoundError(f"未能从 {REPO}:{tag} 提取 vaultwarden 二进制")


def fetch_web_vault(version: str, docker_arch: str, dest_dir: Path) -> Path:
    """
    从官方 -alpine 镜像提取 web-vault 静态前端。
    纯 JS/CSS/HTML，不含本地代码，不依赖 CPU 指令集，直接取官方原版。
    """
    tag = f"{version}-alpine"
    dest_dir.mkdir(parents=True, exist_ok=True)
    webvault = dest_dir / "web-vault"

    if _docker_ok():
        cname = f"vw_web_{docker_arch}_{os.getpid()}"
        platform = f"linux/{docker_arch}"
        subprocess.run(
            ["docker", "pull", "--platform", platform, f"{REPO}:{tag}"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            subprocess.run(
                ["docker", "create", "--name", cname, "--platform", platform, f"{REPO}:{tag}"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["docker", "cp", f"{cname}:/web-vault", str(webvault)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return webvault
        finally:
            subprocess.run(
                ["docker", "rm", "-f", cname],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    webvault.mkdir(parents=True, exist_ok=True)
    token = _registry_token()
    digest = _arch_manifest_digest(tag, docker_arch, token)
    manifest = _image_manifest(digest, token)

    got = False
    for t in _iter_layer_tars(manifest, token):
        members = [
            m for m in t.getmembers()
            if m.name == "web-vault" or m.name.startswith("web-vault/")
        ]
        if not members:
            continue
        for m in members:
            if m.isdir():
                continue
            rel = m.name[len("web-vault/"):].lstrip("/")
            if not rel:
                continue
            tf = webvault / rel
            tf.parent.mkdir(parents=True, exist_ok=True)
            with open(tf, "wb") as out:
                shutil.copyfileobj(t.extractfile(m), out)
            tf.chmod(0o644)
        got = True
        break

    if not got:
        raise FileNotFoundError(f"未能从 {REPO}:{tag} 提取 web-vault")
    return webvault
