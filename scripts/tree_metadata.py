"""比较两个 rootfs 的内容、身份、链接关系和全部扩展属性。"""

import errno
import os
import stat
from collections import defaultdict
from pathlib import Path

from piano_artifacts import sha256


def inventory(root):
    root = Path(root)
    rows, links = {}, defaultdict(list)

    def visit(p, name):
        s = p.lstat()
        row = {"type": stat.S_IFMT(s.st_mode), "mode": stat.S_IMODE(s.st_mode),
               "uid": s.st_uid, "gid": s.st_gid, "mtime_ns": s.st_mtime_ns}
        # ACL、capability、SELinux 与 user.* 都逐个读取。读取错误必须失败，
        # 但"列到过的属性在读取时已不存在"不是差异：宿主（OrbStack/VirtioFS 注入的
        # com.apple.provenance）会在 listxattr 与 getxattr 之间把它清掉，旧行为会
        # 因此中断构建与比对。ENODATA/ENOATTR 视为该属性不存在，其余错误照旧抛出。
        def read_xattr(key):
            try:
                return os.getxattr(p, key, follow_symlinks=False).hex()
            except OSError as error:
                gone = {errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA)}
                if error.errno in gone:
                    return None
                raise
        row["xattrs"] = {key: value for key in sorted(os.listxattr(p, follow_symlinks=False))
                         if (value := read_xattr(key)) is not None}
        if stat.S_ISREG(s.st_mode):
            row.update(bytes=s.st_size, sha256=sha256(p))
            links[s.st_dev, s.st_ino].append(name)
        elif stat.S_ISLNK(s.st_mode):
            row["target"] = os.readlink(p)
        elif not stat.S_ISDIR(s.st_mode):
            raise ValueError(f"unsupported runtime/special file in rootfs: {name}")
        rows[name] = row
        if stat.S_ISDIR(s.st_mode):
            with os.scandir(p) as stream:
                children = sorted(stream, key=lambda e: e.name)
            for child in children:
                visit(Path(child.path), child.name if name == "." else name + "/" + child.name)

    visit(root, ".")
    for group in links.values():
        for name in group:
            rows[name]["hardlinks"] = sorted(group)
    return rows


def compare(source, destination):
    left, right = inventory(source), inventory(destination)
    different = [name for name in sorted(left.keys() | right.keys())
                 if left.get(name) != right.get(name)]
    if different:
        details = []
        for name in different[:5]:
            a, b = left.get(name), right.get(name)
            fields = [key for key in sorted((a or {}).keys() | (b or {}).keys())
                      if (a or {}).get(key) != (b or {}).get(key)]
            details.append(f"{name}: {', '.join(fields) or 'entry missing'}")
        raise ValueError("rootfs metadata/content mismatch: " + "; ".join(details))
    return {"entries": len(left), "file_content_checked": True,
            "numeric_owner_checked": True, "mode_checked": True,
            "symlink_targets_checked": True, "hardlink_groups_checked": True,
            "mtime_checked": True, "xattr_checked": True,
            "xattr_read_errors_ignored": False,
            "capability_files": sum("security.capability" in row["xattrs"] for row in left.values()),
            "selinux_labels": sum("security.selinux" in row["xattrs"] for row in left.values()),
            "acl_files": sum(any(k.startswith("system.posix_acl_") for k in row["xattrs"])
                             for row in left.values())}
