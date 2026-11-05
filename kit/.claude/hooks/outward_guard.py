#!/usr/bin/env python3
"""PreToolUse(Bash) gate: any command that writes to a shared system returns permissionDecision "ask",
so it needs the owner's explicit yes even in auto mode. Read-only and local commands pass through untouched."""
import json
import re
import sys

LOCAL_TARGET = re.compile(r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|host\.docker\.internal)\b")

OUTWARD = [
    (r"\bgit\s+(?:-[cC]\s+\S+\s+)*push\b", "git push"),
    (r"\bgh\s+pr\s+(create|comment|review|merge|edit|close|reopen|ready|lock)\b", "GitHub PR write"),
    (r"\bgh\s+issue\s+(create|comment|edit|close|reopen|delete|transfer)\b", "GitHub issue write"),
    (r"\bgh\s+release\s+(create|edit|delete|upload|delete-asset)\b", "GitHub release"),
    (r"\bgh\s+workflow\s+(run|enable|disable)\b", "workflow dispatch"),
    (r"\bgh\s+run\s+(rerun|cancel|delete)\b", "workflow run change"),
    (r"\bgh\s+(repo\s+(create|delete|edit|fork|rename|archive)|label\s+(create|edit|delete)|secret\s+(set|delete)|variable\s+(set|delete))\b",
     "GitHub repo/settings write"),
    (r"\bdocker\s+push\b", "image push"),
    (r"\b(npm|pnpm|yarn)\s+publish\b|\bgradlew?\s+[^|;&]*\bpublish\w*", "package publish"),
    (r"\bkubectl\s+(apply|delete|edit|patch|scale|rollout|create|replace)\b", "Kubernetes change"),
    (r"\baws\s+\S+\s+(put|create|delete|update|modify|start|stop|terminate|run)[\w-]*\b", "AWS write"),
]


def gh_graphql_write(segment, command):
    """GraphQL always goes out as POST, so only the query tells a read from a write. A mutation anywhere in the
    command (the query may span lines), a query read from a file, or a raw --input body counts as a write."""
    method = re.search(r"(?:-X|--method)[\s=]+([A-Za-z]+)", segment)
    if method and method.group(1).upper() not in ("GET", "POST"):
        return True
    if re.search(r"\s--input\b|\s(-f|-F|--field|--raw-field)[\s=]+query=@", segment):
        return True
    return bool(re.search(r"\bmutation\b", command))


def gh_api_write(command):
    for segment in re.split(r"&&|\|\||;|\n", command):
        if not re.search(r"\bgh\s+api\b", segment):
            continue
        if re.search(r"\bgh\s+api\s+(?:-\S+\s+)*graphql\b", segment):
            if gh_graphql_write(segment, command):
                return True
            continue
        method = re.search(r"(?:-X|--method)[\s=]+([A-Za-z]+)", segment)
        if method and method.group(1).upper() != "GET":
            return True
        if not method and re.search(r"\s(-f|-F|--field|--raw-field|--input)\b", segment):
            return True  # gh api defaults to POST when fields are given
    return False


def http_write(command):
    for segment in re.split(r"&&|\|\||;|\n", command):
        if not re.search(r"(^|[\s(|])(curl|wget)\b|^\s*(http|https|xh)\s", segment):
            continue
        writes = re.search(r"(-X|--request)[\s=]*(POST|PUT|PATCH|DELETE)\b|\s(-d|--data\S*|--json|--form|-F|--upload-file|-T)\s"
                           r"|\b(POST|PUT|PATCH|DELETE)\s+https?://", segment, re.IGNORECASE)
        urls = re.findall(r"https?://\S+", segment)
        if writes and not (urls and all(LOCAL_TARGET.match(url) for url in urls)):
            return True
    return False


def reason_for(command):
    for pattern, label in OUTWARD:
        if re.search(pattern, command):
            return label
    if gh_api_write(command):
        return "GitHub API write"
    if http_write(command):
        return "HTTP write to a remote host"
    return None


def main():
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return
    label = reason_for(payload.get("tool_input", {}).get("command", ""))
    if label:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"Autopilot gate: {label} is an outward action and needs your explicit yes.",
        }}))


if __name__ == "__main__":
    main()
