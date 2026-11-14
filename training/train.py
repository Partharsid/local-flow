"""Fine-tune a small Qwen3 model on LocalFlow dictation data (LoRA, bf16).

    python training/train.py --base models/Qwen3-1.7B --out runs/q17 --epochs 2

Trains on synthetic data + hand-written examples (and the user's own
corrections, if exported), then merges the LoRA weights so the result is a
plain model folder ready for GGUF conversion.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from torch.utils.checkpoint import checkpoint
from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from localflow.tuned import END, build_prompt  # noqa: E402


def rows_from(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8")] if path.exists() else []


def load_rows(gold_weight: int, user_weight: int) -> list[dict]:
    import gold
    rows = rows_from(HERE / "data" / "synth_train.jsonl")
    g = [dict(mode=m, app=a, vocab=v, input=i, output=o) for m, a, v, i, o in gold.T]
    rows += g * gold_weight
    rows += rows_from(HERE / "data" / "user_corrections.jsonl") * user_weight
    rows += rows_from(HERE / "data" / "realistic.jsonl")
    return rows


def encode(tok, r: dict, max_len: int):
    p = tok(build_prompt(r["mode"], r["app"], r["vocab"], r["input"]), add_special_tokens=False)["input_ids"]
    a = tok(r["output"] + END + "\n", add_special_tokens=False)["input_ids"]
    ids = (p + a)[:max_len]
    labels = ([-100] * len(p) + a)[:max_len]
    return ids, labels


def batches(data, tokens_per_batch: int):
    """Similar lengths together; each batch holds about tokens_per_batch tokens."""
    data = sorted(data, key=lambda x: len(x[0]))
    out, cur, longest = [], [], 0
    for ex in data:
        longest2 = max(longest, len(ex[0]))
        if cur and longest2 * (len(cur) + 1) > tokens_per_batch:
            out.append(cur)
            cur, longest2 = [], len(ex[0])
        cur.append(ex)
        longest = longest2
    if cur:
        out.append(cur)
    random.shuffle(out)
    return out


def chunk_loss(h, y, w):
    logits = torch.nn.functional.linear(h, w).float()
    return torch.nn.functional.cross_entropy(logits, y, reduction="sum")


def collate(batch, pad_id):
    n = max(len(x[0]) for x in batch)
    ids = torch.full((len(batch), n), pad_id)
    lab = torch.full((len(batch), n), -100)
    att = torch.zeros((len(batch), n), dtype=torch.long)
    for i, (x, y) in enumerate(batch):
        ids[i, :len(x)] = torch.tensor(x)
        lab[i, :len(y)] = torch.tensor(y)
        att[i, :len(x)] = 1
    return ids, lab, att


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(HERE / "models" / "Qwen3-1.7B"))
    ap.add_argument("--out", default=str(HERE / "runs" / "q17"))
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--tokens", type=int, default=6144, help="tokens per micro-batch")
    ap.add_argument("--accum", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=768)
    ap.add_argument("--gold-weight", type=int, default=6)
    ap.add_argument("--user-weight", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    random.seed(0)
    torch.manual_seed(0)

    tok = AutoTokenizer.from_pretrained(a.base)
    rows = load_rows(a.gold_weight, a.user_weight)
    random.shuffle(rows)
    if a.limit:
        rows = rows[:a.limit]
    data = [encode(tok, r, a.max_len) for r in rows]
    print(f"{len(data)} examples, {sum(len(x[0]) for x in data) / 1e6:.2f}M tokens", flush=True)

    # Fail fast instead of letting Windows move VRAM into (30x slower) system RAM.
    torch.cuda.set_per_process_memory_fraction(0.88)
    model = AutoModelForCausalLM.from_pretrained(a.base, torch_dtype=torch.bfloat16, attn_implementation="sdpa").cuda()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()
    model.config.use_cache = False
    lcfg = LoraConfig(r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.05, bias="none", task_type="CAUSAL_LM",
                      target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, lcfg)
    model.print_trainable_parameters()

    epoch_batches = len(batches(data, a.tokens))
    total = math.ceil(epoch_batches * a.epochs / a.accum)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=a.lr, weight_decay=0.0)
    sched = get_cosine_schedule_with_warmup(opt, min(60, total // 10), total)
    model.train()
    step, t0, run_loss, seen = 0, time.time(), 0.0, 0
    out = Path(a.out)
    ckpt = out / "checkpoint"
    if (ckpt / "state.json").exists():          # resume after an interruption
        from peft import set_peft_model_state_dict
        from safetensors.torch import load_file
        set_peft_model_state_dict(model, load_file(ckpt / "adapter_model.safetensors"))
        opt.load_state_dict(torch.load(ckpt / "optimizer.pt"))
        step = json.loads((ckpt / "state.json").read_text())["step"]
        for _ in range(step):
            sched.step()
        print(f"resumed at step {step}", flush=True)
    start_step = step
    done = step >= total
    ep = int(step * a.accum / max(1, epoch_batches))
    while not done:
        bs = batches(data, a.tokens)
        if a.epochs - ep < 1:
            bs = bs[:int(len(bs) * (a.epochs - ep))]
        for i, b in enumerate(bs):
            ids, lab, att = (t.cuda() for t in collate(b, tok.pad_token_id))
            # Vocabulary logits only for the answer tokens: the full logits
            # (151k words x every token) would not fit in 8 GB.
            base = model.get_base_model()
            try:
                hidden = base.model(input_ids=ids, attention_mask=att).last_hidden_state[:, :-1]
                target = lab[:, 1:]
                keep = target != -100
                h, y = hidden[keep], target[keep]
                if not len(y):
                    continue
                # In chunks, recomputed in backward: full fp32 logits for ~1000
                # tokens x 151k words would take ~2 GB and spill out of VRAM.
                total_loss = 0.0
                for c in range(0, len(y), 256):
                    total_loss = total_loss + checkpoint(chunk_loss, h[c:c + 256], y[c:c + 256], base.lm_head.weight,
                                                         use_reentrant=False)
                loss = total_loss / len(y) / a.accum
                loss.backward()
            except torch.OutOfMemoryError:
                print("skipped a batch that did not fit in memory", flush=True)
                opt.zero_grad(set_to_none=True)
                torch.cuda.empty_cache()
                continue
            run_loss += loss.item()
            seen += 1
            if seen % a.accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step(); sched.step(); opt.zero_grad(set_to_none=True)
                step += 1
                if step % 20 == 0 or step == 1:
                    el = time.time() - t0
                    print(f"step {step}/{total} loss {run_loss / (20 if step > 1 else 1):.4f} "
                          f"lr {sched.get_last_lr()[0]:.2e} {el / 60:.1f} min, "
                          f"eta {el / max(1, step - start_step) * (total - step) / 60:.1f} min", flush=True)
                    run_loss = 0.0
                if step % 20 == 0:
                    model.save_pretrained(ckpt)
                    torch.save(opt.state_dict(), ckpt / "optimizer.pt")
                    (ckpt / "state.json").write_text(json.dumps({"step": step}))
                if step >= total:
                    done = True
                    break
        ep += 1
        if ep >= math.ceil(a.epochs):
            done = True
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out / "lora")
    merged = model.merge_and_unload()
    merged.config.use_cache = True
    merged.save_pretrained(out / "merged", safe_serialization=True)
    tok.save_pretrained(out / "merged")
    for f in ("generation_config.json",):
        src = Path(a.base) / f
        if src.exists():
            (out / "merged" / f).write_bytes(src.read_bytes())
    print("saved", out / "merged", f"in {(time.time() - t0) / 60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
