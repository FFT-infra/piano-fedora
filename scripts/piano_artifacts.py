"""构建产物共用的身份、摘要与 guest 路径检查。"""

import hashlib
import json
import re
import shutil
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


@contextmanager
def artifact_output(path):
    """仅清理由本次调用新建的失败产物，不删除或覆盖已有输出。"""
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    except BaseException:
        shutil.rmtree(path)
        raise


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def uefi_patch_identities(project):
    """UEFI 实际消费的项目补丁和应用入口，不绑定无关 Linux 补丁。"""
    project = Path(project)
    manifests = sorted((project / "patches").glob("uefi-*/series.json"))
    if not manifests:
        raise ValueError("UEFI patch series missing")
    names = ["scripts/apply-patches.py"]
    for manifest in manifests:
        metadata = json.loads(manifest.read_text())
        names.append(manifest.relative_to(project).as_posix())
        for row in metadata["patches"]:
            name = row["file"]
            if not name or Path(name).name != name or name in (".", ".."):
                raise ValueError("invalid UEFI patch path")
            names.append((manifest.parent / name).relative_to(project).as_posix())
    return {name: sha256(project / name) for name in names}


def verify_uefi_source(project, image, record, run_id):
    expected = json.loads((Path(project) / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    if (record.get("schema_version") != 1 or record.get("run_id") != str(run_id)
            or not re.fullmatch(r"[1-9][0-9]*", str(run_id))
            or record.get("workflow") != ".github/workflows/build-uefi.yml"
            or not re.fullmatch(r"[0-9a-f]{40}", record.get("producer_commit", ""))
            or record.get("sunuefi_commit") != expected
            or record.get("project_source_sha256") != uefi_patch_identities(project)):
        raise ValueError("UEFI provenance differs from its run or pinned source/patches")
    if record.get("product") != {"sha256": sha256(image), "bytes": Path(image).stat().st_size}:
        raise ValueError("UEFI provenance names a different product image")
    evidence = record.get("evidence", {})
    if (evidence.get("kind") != "successful-actions-build-log"
            or not re.fullmatch(r"[0-9a-f]{64}", evidence.get("log_sha256", ""))):
        raise ValueError("UEFI provenance is missing its actual build evidence")


def safe_relative(name):
    p = PurePosixPath(name)
    if p.is_absolute() or not name or any(x in ("", ".", "..") for x in name.split("/")):
        raise ValueError(f"unsafe relative path: {name!r}")
    return p


def guest_resolve(root, name):
    """guest 的绝对链接从 guest root 重新解析，绝不读取宿主目标。"""
    root = Path(root).resolve()
    pending, parts, hops = name.lstrip("/").split("/"), [], 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError("guest link escapes root")
            parts.pop()
            continue
        candidate = root.joinpath(*parts, part)
        if candidate.is_symlink():
            hops += 1
            if hops > 40:
                raise ValueError("guest symlink cycle")
            target = str(candidate.readlink())
            if target.startswith("/"):
                parts = []
            pending = target.split("/") + pending
        else:
            parts.append(part)
    return root.joinpath(*parts)


def safe_destination(root, name):
    """写入用 /usr 等 canonical 路径，拒绝经过任意 guest 符号链接。"""
    safe_relative(str(name))
    root = Path(root).resolve()
    p = root / name
    for parent in (p, *p.parents):
        if parent == root:
            return p
        if parent.is_symlink():
            raise ValueError(f"destination traverses a symlink: {name}")
    raise ValueError(f"destination is outside root: {name}")


def inspect_kernel(kernel, require_f2fs=True):
    """验证已封存的 Image、配置、每个模块及全部 modules.* 索引。"""
    kernel = Path(kernel).resolve()
    m = json.loads((kernel / "manifest.json").read_text())
    if m.get("status") != "HOST_BUILT_FULL_CANDIDATE_NOT_HARDWARE_VERIFIED" or not m.get("source_clean"):
        raise ValueError("kernel is not a completed clean full build")
    release = m.get("kernel_release", "")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", release):
        raise ValueError("invalid kernel release")
    for name, digest in (("Image", m["image"]["sha256"]), ("config", m["config_sha256"])):
        if sha256(kernel / name) != digest:
            raise ValueError(f"sealed kernel {name} changed")
    config = dict(line.split("=", 1) for line in (kernel / "config").read_text().splitlines()
                  if line.startswith("CONFIG_") and "=" in line)
    if require_f2fs:
        for symbol in ("F2FS_FS", "F2FS_FS_XATTR", "F2FS_FS_POSIX_ACL", "F2FS_FS_SECURITY"):
            if config.get("CONFIG_" + symbol) != "y":
                raise ValueError(f"kernel needs built-in CONFIG_{symbol}=y")
    if m.get("root_policy") != "LABEL=PIANOROOT":
        raise ValueError("kernel does not select the dedicated piano root")
    rows = m.get("modules", [])
    if not rows:
        raise ValueError("empty sealed module set")
    expected = set()
    for row in rows:
        name = row["path"]
        safe_relative(name)
        if not name.startswith(f"lib/modules/{release}/") or not name.endswith(".ko"):
            raise ValueError(f"module path/release mismatch: {name}")
        if name in expected or not row["vermagic"].startswith(release + " "):
            raise ValueError(f"module ABI or duplicate mismatch: {name}")
        expected.add(name)
        p = kernel / "modules" / name
        if p.is_symlink() or sha256(p) != row["sha256"] or p.stat().st_size != row["bytes"]:
            raise ValueError(f"sealed module changed: {name}")
    actual = {p.relative_to(kernel / "modules").as_posix()
              for p in (kernel / "modules").rglob("*.ko")}
    if actual != expected:
        raise ValueError("sealed module set differs from manifest")
    indexes = m["module_summary"]["index_sha256"]
    for name, digest in indexes.items():
        safe_relative(name)
        if not name.startswith(f"lib/modules/{release}/") or sha256(kernel / "modules" / name) != digest:
            raise ValueError(f"sealed module index changed: {name}")
    module_dir = kernel / "modules/lib/modules" / release
    if set(indexes) != {p.relative_to(kernel / "modules").as_posix() for p in module_dir.glob("modules.*") if p.is_file()}:
        raise ValueError("module index set differs from manifest")
    return m, sha256(kernel / "manifest.json")


def inspect_installed_kernel(root, boot_dir="boot"):
    """验证已安装系统里的内核就是封存 manifest 描述的那一份。

    `inspect_kernel()` 只在构建机上成立：它要求 `modules.*` 索引逐字节等于
    封存值、模块集合精确等于 manifest。装进系统之后这两条永远不再成立——
    `depmod` 会重写索引，外部模块会落进 `updates/`。设备侧因此换一组判据，
    验的是包管理器不会改写的部分：

    - `/usr/share/piano/kernel-manifest.json` 必须是完整的封存记录；
    - `/boot/vmlinuz-*` 里必须有一份的 sha256 与字节数等于 manifest 的 Image；
    - manifest 列出的每个模块在 `/lib/modules/<release>/` 下存在且摘要相符。

    `updates/` 与 `modules.*` 索引一律不看。installonly 让新旧内核共存，
    所以 Image 按内容匹配，不按文件名匹配。返回 `(manifest, manifest_sha256,
    image_path)`。
    """
    root = Path(root)
    manifest_path = root / "usr/share/piano/kernel-manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("installed kernel manifest missing: /usr/share/piano/kernel-manifest.json")
    m = json.loads(manifest_path.read_text())
    if m.get("status") != "HOST_BUILT_FULL_CANDIDATE_NOT_HARDWARE_VERIFIED" or not m.get("source_clean"):
        raise ValueError("installed kernel is not a completed clean full build")
    release = m.get("kernel_release", "")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", release):
        raise ValueError("invalid kernel release")
    if m.get("root_policy") != "LABEL=PIANOROOT":
        raise ValueError("installed kernel does not select the dedicated piano root")

    image = m.get("image", {})
    if not re.fullmatch(r"[0-9a-f]{64}", image.get("sha256", "")):
        raise ValueError("installed kernel manifest has no Image digest")
    candidates = sorted(p for p in (root / boot_dir).glob("vmlinuz-*")
                        if p.is_file() and not p.is_symlink())
    matches = [p for p in candidates
               if p.stat().st_size == image.get("bytes") and sha256(p) == image["sha256"]]
    if not matches:
        raise ValueError(f"no {boot_dir}/vmlinuz-* matches the sealed kernel Image "
                         f"({len(candidates)} present)")

    rows = m.get("modules", [])
    if not rows:
        raise ValueError("empty sealed module set")
    for row in rows:
        name = row["path"]
        safe_relative(name)
        if not name.startswith(f"lib/modules/{release}/") or not name.endswith(".ko"):
            raise ValueError(f"module path/release mismatch: {name}")
        if not row["vermagic"].startswith(release + " "):
            raise ValueError(f"module ABI mismatch: {name}")
        installed = guest_resolve(root, "/" + name)
        if (installed.is_symlink() or not installed.is_file()
                or installed.stat().st_size != row["bytes"]
                or sha256(installed) != row["sha256"]):
            raise ValueError(f"installed module differs from the sealed kernel: {name}")
    return m, sha256(manifest_path), matches[0]
