import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ida_bytes
import ida_funcs
import ida_ida
import ida_kernwin
import ida_loader
import ida_nalt
import ida_name
import ida_segment
import ida_ua
import idautils
import idc


HOST = "127.0.0.1"
PORT = 8765
DEFAULT_PATTERN_BYTES = 32
DEFAULT_MIN_EXACT = 10
_SERVER = None


def log(message):
    ida_kernwin.msg(f"[pattern-bridge] {message}\n")


def normalize_signature(value):
    return " ".join(str(value).strip().split()).upper()


def tokenize_signature(signature):
    return [token for token in normalize_signature(signature).split() if token]


def looks_like_signature(signature):
    tokens = tokenize_signature(signature)
    if len(tokens) < 3:
        return False
    for token in tokens:
        if token in {"?", "??"}:
            continue
        if len(token) != 2:
            return False
        try:
            int(token, 16)
        except ValueError:
            return False
    return True


def wildcard_token(token):
    return token in {"?", "??"}


def get_all_named_functions():
    items = []
    for ea in idautils.Functions():
        name = ida_funcs.get_func_name(ea) or idc.get_func_name(ea) or ""
        if name:
            items.append((name, ea))
    return items


def find_function_ea_by_name(name, case_sensitive):
    if not name:
        return idc.BADADDR
    exact = ida_name.get_name_ea(idc.BADADDR, name)
    if exact != idc.BADADDR:
        func = ida_funcs.get_func(exact)
        return func.start_ea if func else exact

    wanted = name if case_sensitive else name.lower()
    for candidate_name, ea in get_all_named_functions():
        cmp_name = candidate_name if case_sensitive else candidate_name.lower()
        if cmp_name == wanted:
            return ea
    return idc.BADADDR


def get_segment_cache():
    cache = []
    for seg_ea in idautils.Segments():
        seg = ida_segment.getseg(seg_ea)
        if seg is None:
            continue
        start = seg.start_ea
        end = seg.end_ea
        size = end - start
        if size <= 0:
            continue
        data = ida_bytes.get_bytes(start, size)
        if not data:
            continue
        cache.append((start, end, data))
    return cache


def find_pattern_ea(signature_tokens, segment_cache):
    if not signature_tokens:
        return idc.BADADDR

    token_count = len(signature_tokens)
    for seg_start, seg_end, data in segment_cache:
        limit = len(data) - token_count + 1
        if limit < 1:
            continue
        for offset in range(limit):
            matched = True
            for idx, token in enumerate(signature_tokens):
                if wildcard_token(token):
                    continue
                if data[offset + idx] != int(token, 16):
                    matched = False
                    break
            if matched:
                return seg_start + offset
    return idc.BADADDR


def build_pattern_from_existing_mask(ea, old_tokens):
    data = ida_bytes.get_bytes(ea, len(old_tokens))
    if not data or len(data) < len(old_tokens):
        return None
    new_tokens = []
    for idx, token in enumerate(old_tokens):
        if wildcard_token(token):
            new_tokens.append("?")
        else:
            new_tokens.append(f"{data[idx]:02X}")
    return " ".join(new_tokens)


def should_mask_operand(op):
    operand_type = int(getattr(op, "type", 0))
    return operand_type in {
        ida_ua.o_imm,
        ida_ua.o_mem,
        ida_ua.o_near,
        ida_ua.o_far,
        ida_ua.o_displ,
        ida_ua.o_phrase,
    }


def build_function_pattern(start_ea, max_bytes=DEFAULT_PATTERN_BYTES, min_exact=DEFAULT_MIN_EXACT):
    tokens = []
    exact_count = 0
    ea = start_ea
    end_limit = start_ea + max_bytes * 4

    while ea != idc.BADADDR and ea < end_limit and len(tokens) < max_bytes:
        insn = ida_ua.insn_t()
        size = ida_ua.decode_insn(insn, ea)
        if size <= 0:
            break

        data = ida_bytes.get_bytes(ea, size)
        if not data or len(data) < size:
            break

        wildcard_from = None
        for op in insn.ops:
            offb = int(getattr(op, "offb", 0))
            if offb <= 0:
                continue
            if should_mask_operand(op):
                wildcard_from = offb if wildcard_from is None else min(wildcard_from, offb)

        for idx, byte in enumerate(data):
            if len(tokens) >= max_bytes:
                break
            if wildcard_from is not None and idx >= wildcard_from:
                tokens.append("?")
            else:
                tokens.append(f"{byte:02X}")
                exact_count += 1

        if exact_count >= min_exact and len(tokens) >= 12:
            break
        ea += size

    if exact_count < min_exact or len(tokens) < 6:
        return None
    return " ".join(tokens)


def resolve_pattern(entry, case_sensitive, segment_cache):
    name = str(entry.get("name", "")).strip()
    old_value = normalize_signature(entry.get("old_value", ""))
    result = {
        "name": name,
        "old_value": old_value,
        "new_value": old_value,
        "status": "Not Found",
        "source": "",
    }

    if not looks_like_signature(old_value):
        return result

    old_tokens = tokenize_signature(old_value)
    match_ea = find_pattern_ea(old_tokens, segment_cache)
    if match_ea != idc.BADADDR:
        new_pattern = build_pattern_from_existing_mask(match_ea, old_tokens)
        if new_pattern:
            func = ida_funcs.get_func(match_ea)
            if func:
                source_name = ida_funcs.get_func_name(func.start_ea) or f"0x{func.start_ea:X}"
                result["source"] = f"{source_name} @ 0x{match_ea:X}"
            else:
                result["source"] = f"0x{match_ea:X}"
            result["new_value"] = new_pattern
            result["status"] = "Found Same" if new_pattern == old_value else "Updated"
            return result

    func_ea = find_function_ea_by_name(name, case_sensitive)
    if func_ea != idc.BADADDR:
        new_pattern = build_function_pattern(func_ea)
        if new_pattern:
            result["new_value"] = new_pattern
            result["status"] = "Found Same" if new_pattern == old_value else "Updated"
            result["source"] = f"{ida_funcs.get_func_name(func_ea) or name} @ 0x{func_ea:X}"
            return result

    return result


class PatternBridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_json({"ok": False, "error": "Unknown endpoint"}, status=404)
            return
        self.send_json(
            {
                "ok": True,
                "version": ida_kernwin.get_kernel_version(),
                "input_file": ida_nalt.get_input_file_path(),
                "database_path": idc.get_idb_path(),
            }
        )

    def do_POST(self):
        if self.path != "/update-patterns":
            self.send_json({"ok": False, "error": "Unknown endpoint"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else "{}"
            payload = json.loads(raw)
        except Exception as ex:
            self.send_json({"ok": False, "error": f"Invalid request: {ex}"}, status=400)
            return

        entries = payload.get("entries", [])
        case_sensitive = bool(payload.get("case_sensitive", False))
        if not isinstance(entries, list):
            self.send_json({"ok": False, "error": "entries must be a list"}, status=400)
            return

        segment_cache = get_segment_cache()
        results = [resolve_pattern(entry, case_sensitive, segment_cache) for entry in entries if isinstance(entry, dict)]
        self.send_json(
            {
                "ok": True,
                "input_file": ida_nalt.get_input_file_path(),
                "database_path": idc.get_idb_path(),
                "results": results,
            }
        )

    def log_message(self, format, *args):
        return

    def send_json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_server():
    global _SERVER
    if _SERVER is not None:
        log(f"already listening on http://{HOST}:{PORT}")
        return
    server = ThreadingHTTPServer((HOST, PORT), PatternBridgeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _SERVER = server
    log(f"listening on http://{HOST}:{PORT} for {idc.get_idb_path()}")


start_server()
