#!/usr/bin/env python3
"""编译 SunUEFI 里真正要发货的 Gunyah RM 交接代码，跑接受与拒绝路径。

这里不做字节级 DTB 对照：那需要对真实 libfdt 跑真实抓取的 payload DTB 与
继承 FDT 对照，证据在宿主侧工作目录（见 docs/device-acceptance-20261015.md）。
本测试负责的是策略本身——把 uefi-live-memory + uefi-live-rm 之后的原文抽出来，
配一个语义等价的假 FdtLib 编译，确认只有「恰好一个匹配节点 + 16 字节非零
capability 对」才会改写目标树的 reg，其余输入一律保留原值。

没有 SunUEFI checkout 时跳过，与 check_patches.py 的约定一致。
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from piano_artifacts import uefi_patch_identities

SOURCE = "uefi/core/PianoRawLinuxBoot.c"
SERIES = ("uefi-live-memory", "uefi-live-rm")
RM_START = "// The Gunyah resource manager is reached with two capabilities the bootloader"
RM_END = "EFI_STATUS PianoRawLinuxPrepare("

FAKE_FDT = r"""
/* 假 FdtLib：语义与 MdePkg/BaseFdtLib 在这条路径上用到的一致。
   节点只能按 compatible 命中，reg 是按字节存的 16 字节大端对。 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned char BOOLEAN;
typedef uint8_t UINT8;typedef uint16_t UINT16;typedef uint32_t UINT32;typedef uint64_t UINT64;
typedef int32_t INT32;typedef uint64_t EFI_STATUS;typedef size_t UINTN;typedef void VOID;typedef char CHAR8;
#define CONST const
#define STATIC static
#define TRUE 1
#define FALSE 0
#define EFI_SUCCESS ((EFI_STATUS)0)
#define EFI_COMPROMISED_DATA ((EFI_STATUS)0x8000000000000013ULL)
#define MAX_UINT64 0xFFFFFFFFFFFFFFFFULL
#define DEBUG_WARN 1
#define DEBUG(Args) FakeDebug Args

typedef struct {CONST CHAR8 *Compatible;BOOLEAN HasReg;INT32 RegBytes;UINT8 Reg[16];} FAKE_NODE;
typedef struct {BOOLEAN Invalid;INT32 Count;FAKE_NODE Node[4];} FAKE_FDT;

STATIC VOID ZeroMem(VOID *P,UINTN N){memset(P,0,N);}
STATIC VOID CopyMem(VOID *D,CONST VOID *S,UINTN N){memcpy(D,S,N);}
STATIC UINT64 ReadUnaligned64(CONST UINT64 *P){UINT64 V;memcpy(&V,P,sizeof(V));return V;}
STATIC UINT64 SwapBytes64(UINT64 V){return __builtin_bswap64(V);}
/* 日志不是这条测试的判据，策略结果由 CASE 行给出。 */
STATIC VOID FakeDebug(UINTN Level,CONST CHAR8 *Fmt,...){(VOID)Level;(VOID)Fmt;}
STATIC INT32 FdtCheckHeader(CONST VOID *Fdt){return ((CONST FAKE_FDT *)Fdt)->Invalid?-1:0;}
STATIC INT32 FdtTotalSize(CONST VOID *Fdt){(VOID)Fdt;return 4096;}
STATIC INT32 FdtNodeOffsetByCompatible(CONST VOID *Fdt,INT32 Start,CONST CHAR8 *Compatible){
  CONST FAKE_FDT *F=(CONST FAKE_FDT *)Fdt;
  for(INT32 I=Start+1;I<F->Count;++I)if(!strcmp(F->Node[I].Compatible,Compatible))return I;
  return -1;
}
STATIC CONST VOID *FdtGetProp(CONST VOID *Fdt,INT32 Node,CONST CHAR8 *Name,INT32 *Length){
  CONST FAKE_FDT *F=(CONST FAKE_FDT *)Fdt;
  if(Node<0||Node>=F->Count||strcmp(Name,"reg")||!F->Node[Node].HasReg){
    if(Length)*Length=-1;return NULL;}
  if(Length)*Length=F->Node[Node].RegBytes;
  return F->Node[Node].Reg;
}
STATIC INT32 FdtSetProp(VOID *Fdt,INT32 Node,CONST CHAR8 *Name,CONST VOID *Value,UINT32 Length){
  FAKE_FDT *F=(FAKE_FDT *)Fdt;
  if(Node<0||Node>=F->Count||strcmp(Name,"reg")||Length!=16)return -1;
  CopyMem(F->Node[Node].Reg,Value,16);F->Node[Node].RegBytes=16;F->Node[Node].HasReg=TRUE;
  return 0;
}

STATIC FAKE_FDT *FakeNew(VOID){return calloc(1,sizeof(FAKE_FDT));}
STATIC FAKE_NODE *FakeAdd(FAKE_FDT *F,CONST CHAR8 *Compatible){
  FAKE_NODE *N=&F->Node[F->Count++];N->Compatible=Compatible;return N;}
STATIC VOID FakePutU64(UINT8 *Where,UINT64 Value){UINT64 Be=SwapBytes64(Value);memcpy(Where,&Be,8);}
STATIC VOID FakeReg(FAKE_NODE *N,UINT64 Tx,UINT64 Rx,INT32 Bytes){
  FakePutU64(N->Reg,Tx);FakePutU64(N->Reg+8,Rx);N->RegBytes=Bytes;N->HasReg=TRUE;}
"""

CASES = r"""
#define STALE_TX 0xeb3679e7eae8c38dULL
#define STALE_RX 0xeb3679e7eae845e4ULL
#define LIVE_TX 0x0dc5ec1f8ef953adULL
#define LIVE_RX 0x0dc5ec1f8ef9634eULL
/* 0=16 字节非零 1=没有 RM 节点 2=两个 RM 节点 3=8 字节 reg 4=rx 为 0 5=坏魔数 */
STATIC FAKE_FDT *Inherited(UINT32 Mode){
  FAKE_FDT *F=FakeNew();
  if(Mode==5){F->Invalid=TRUE;return F;}
  if(Mode==1)return F;
  FakeReg(FakeAdd(F,"gunyah-resource-manager"),LIVE_TX,Mode==4?0:LIVE_RX,Mode==3?8:16);
  if(Mode==2)FakeReg(FakeAdd(F,"gunyah-resource-manager"),LIVE_TX,LIVE_RX,16);
  return F;
}
/* 0=16 字节旧值 1=没有 RM 节点 2=两个 RM 节点 3=8 字节 reg */
STATIC FAKE_FDT *Target(UINT32 Mode){
  FAKE_FDT *F=FakeNew();
  if(Mode==1)return F;
  FakeReg(FakeAdd(F,"gunyah-resource-manager"),STALE_TX,STALE_RX,Mode==3?8:16);
  if(Mode==2)FakeReg(FakeAdd(F,"gunyah-resource-manager"),STALE_TX,STALE_RX,16);
  return F;
}
STATIC BOOLEAN Adopted(FAKE_FDT *F,UINT64 *Tx,UINT64 *Rx){
  if(F->Count<1||!F->Node[0].HasReg||F->Node[0].RegBytes!=16)return FALSE;
  *Tx=SwapBytes64(ReadUnaligned64((CONST UINT64 *)F->Node[0].Reg));
  *Rx=SwapBytes64(ReadUnaligned64((CONST UINT64 *)(F->Node[0].Reg+8)));
  return TRUE;
}
STATIC INT32 Case(CONST CHAR8 *Name,UINT32 InheritedMode,UINT32 TargetMode,BOOLEAN WantCapture,
                  BOOLEAN WantLive){
  FAKE_FDT *Source=Inherited(InheritedMode),*Tree=Target(TargetMode);
  LIVE_RM Rm;CaptureLiveRm(&Rm,Source);
  EFI_STATUS Status=ApplyLiveRm(Tree,&Rm);
  UINT64 Tx=0,Rx=0;BOOLEAN Live=Adopted(Tree,&Tx,&Rx)&&Tx==LIVE_TX&&Rx==LIVE_RX;
  BOOLEAN Ok=(Rm.Present==WantCapture)&&(Live==WantLive)&&(Status==EFI_SUCCESS);
  printf("CASE %s captured=%u live=%u status=%llu -> %s\n",Name,Rm.Present,Live,
         (unsigned long long)Status,Ok?"PASS":"FAIL");
  free(Source);free(Tree);
  return Ok?0:1;
}
INT32 main(VOID){
  INT32 Failed=0;
  Failed+=Case("inherited+target adopt",0,0,TRUE,TRUE);
  Failed+=Case("inherited without RM node",1,0,FALSE,FALSE);
  Failed+=Case("inherited ambiguous",2,0,FALSE,FALSE);
  Failed+=Case("inherited 8 byte reg",3,0,FALSE,FALSE);
  Failed+=Case("inherited zero rx",4,0,FALSE,FALSE);
  Failed+=Case("inherited bad magic",5,0,FALSE,FALSE);
  Failed+=Case("target without RM node",0,1,TRUE,FALSE);
  Failed+=Case("target ambiguous",0,2,TRUE,FALSE);
  Failed+=Case("target 8 byte reg",0,3,TRUE,FALSE);
  printf("CASES_FAILED=%d\n",Failed);
  return Failed?1:0;
}
"""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def patched_source(sunuefi, work):
    """按 apply-patches.py 的顺序与摘要门槛重放出真正要编译的那份文件。"""
    base = subprocess.run(["git", "-C", str(sunuefi), "show", f"HEAD:{SOURCE}"],
                          capture_output=True, check=True).stdout
    target = work / SOURCE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(base)
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "base"], check=True)
    for name in SERIES:
        series = ROOT / "patches" / name / "series.json"
        manifest = json.loads(series.read_text(encoding="utf-8"))
        for patch in manifest["patches"]:
            for relative, expected in patch.get("targets", {}).items():
                actual = digest(work / relative)
                assert actual == expected, (
                    f"{name}/{patch['file']}: target digest differs from the series entry\n"
                    f"  expected {expected}\n  actual   {actual}")
            subprocess.run(["git", "-C", str(work), "apply",
                            str(series.parent / patch["file"])], check=True)
    return (work / SOURCE).read_text(encoding="utf-8")


def rm_block(text):
    """只抽 RM 交接那一段及其常量，不把整个文件搬进宿主编译。"""
    defines = [line.strip() for line in text.splitlines() if line.startswith("#define RAW_")]
    start = text.index(RM_START)
    end = text.index(RM_END, start)
    return "\n".join(defines) + "\n" + text[start:end]


def main():
    sunuefi = Path(os.environ.get("PIANO_TEST_SUNUEFI", ROOT.parent / "mipad8p-piano"
                                  / "sources" / "Project-SunUEFI"))
    if not (sunuefi / "build.sh").is_file():
        print(f"skip: no SunUEFI checkout at {sunuefi}")
        return 0

    identities = uefi_patch_identities(ROOT)
    for name in SERIES:
        series = json.loads((ROOT / "patches" / name / "series.json").read_text(encoding="utf-8"))
        for relative in [f"patches/{name}/series.json"] + [
                f"patches/{name}/{patch['file']}" for patch in series["patches"]]:
            assert relative in identities, f"{relative} is not bound to the UEFI product"

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        text = patched_source(sunuefi, work / "repo")
        generated = work / "live_rm_check.c"
        generated.write_text(FAKE_FDT + "\n" + rm_block(text) + "\n" + CASES, encoding="utf-8")
        binary = work / "live_rm_check"
        subprocess.run([os.environ.get("CC", "cc"), "-O1", "-std=gnu11", "-w",
                        str(generated), "-o", str(binary)], check=True)
        result = subprocess.run([str(binary)], capture_output=True, text=True, check=False)

    passes = [line for line in result.stdout.splitlines() if line.startswith("CASE ")]
    failures = [line for line in passes if not line.endswith("-> PASS")]
    assert result.returncode == 0, f"live RM policy harness failed:\n{result.stdout}\n{result.stderr}"
    assert "CASES_FAILED=0" in result.stdout, result.stdout
    assert len(passes) == 9, f"expected nine policy cases, ran {len(passes)}"
    assert not failures, "\n".join(failures)
    print(f"uefi-live-rm policy: {len(passes)} cases passed against the shipped C block")
    return 0


if __name__ == "__main__":
    sys.exit(main())
