"""Convert a merged Qwen3 model folder to GGUF (F16) for llama.cpp.

    python training/convert_gguf.py runs/q17/merged runs/q17/model-f16.gguf

A small, Qwen3-only version of llama.cpp's convert_hf_to_gguf.py, using the
`gguf` package. Then quantize with llama-quantize.exe (see quantize step).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import gguf
import numpy as np
import torch
from safetensors.torch import load_file
from transformers import AutoTokenizer


def looks_special(t: str) -> bool:
    return t in ("<pad>", "<mask>") or (t.startswith("<|") and t.endswith("|>"))


def vocab(model_dir: Path, n_vocab: int):
    tok = AutoTokenizer.from_pretrained(model_dir)
    rev = {i: t for t, i in tok.vocab.items()}
    added = tok.get_added_vocab()
    dec = tok.added_tokens_decoder
    tokens, types = [], []
    for i in range(n_vocab):
        if i not in rev:
            tokens.append(f"[PAD{i}]"); types.append(gguf.TokenType.UNUSED)
            continue
        t = rev[i]
        if t in added:
            if (i in dec and dec[i].special) or looks_special(t):
                types.append(gguf.TokenType.CONTROL)
            else:
                types.append(gguf.TokenType.USER_DEFINED)
        else:
            types.append(gguf.TokenType.NORMAL)
        tokens.append(t)
    return tokens, types


def main(src: str, dst: str):
    d = Path(src)
    hp = json.loads((d / "config.json").read_text())
    assert hp["model_type"] == "qwen3", hp["model_type"]
    n_layer = hp["num_hidden_layers"]
    w = gguf.GGUFWriter(dst, "qwen3")
    w.add_name("LocalFlow dictation")
    w.add_context_length(hp["max_position_embeddings"])
    w.add_embedding_length(hp["hidden_size"])
    w.add_block_count(n_layer)
    w.add_feed_forward_length(hp["intermediate_size"])
    w.add_head_count(hp["num_attention_heads"])
    w.add_head_count_kv(hp["num_key_value_heads"])
    head_dim = hp.get("head_dim") or hp["hidden_size"] // hp["num_attention_heads"]
    w.add_key_length(head_dim)
    w.add_value_length(head_dim)
    w.add_rope_freq_base(hp.get("rope_theta", 1000000.0))
    w.add_layer_norm_rms_eps(hp["rms_norm_eps"])
    w.add_file_type(gguf.LlamaFileType.MOSTLY_F16)

    tokens, types = vocab(d, hp["vocab_size"])
    w.add_tokenizer_model("gpt2")
    w.add_tokenizer_pre("qwen2")
    w.add_token_list(tokens)
    w.add_token_types(types)
    special = gguf.SpecialVocab(d, load_merges=True, n_vocab=len(tokens))
    special.add_to_gguf(w)

    names = gguf.get_tensor_name_map(gguf.MODEL_ARCH.QWEN3, n_layer)
    files = sorted(d.glob("*.safetensors"))
    for f in files:
        for name, t in load_file(f).items():
            if name == "lm_head.weight" and hp.get("tie_word_embeddings"):
                continue
            new = names.get_name(name, try_suffixes=(".weight", ".bias"))
            if new is None:
                raise ValueError(f"unknown tensor {name}")
            arr = t.to(torch.float32).numpy()
            arr = arr.astype(np.float32 if arr.ndim == 1 else np.float16)
            w.add_tensor(new, arr)
    w.write_header_to_file()
    w.write_kv_data_to_file()
    w.write_tensors_to_file()
    w.close()
    print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
