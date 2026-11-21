"""Collect Kubernetes rollout diagnostics using only non-secret status fields."""
import argparse
import json
from pathlib import Path
import subprocess


def summary(namespace, context=None):
    base = ["kubectl"]
    if context:
        base += ["--context", context]
    base += ["-n", namespace]
    output = {}
    for resource in ("nodes", "deployments", "statefulsets", "jobs", "pods", "services",
                     "ingresses", "pvc", "networkpolicies", "events"):
        args = base if resource != "nodes" else base[:-2]
        run = subprocess.run([*args, "get", resource, "-o", "json"],
                             capture_output=True, check=False, timeout=20)
        if run.returncode:
            output[resource] = {"unavailable": True}
            continue
        items = json.loads(run.stdout)["items"]
        output[resource] = [entry(resource, item) for item in items
                            if resource != "events" or item.get("type") == "Warning"]
    return output


def entry(resource, item):
    status = item.get("status", {})
    result = {"name": item["metadata"]["name"]}
    for key in ("readyReplicas", "updatedReplicas", "replicas", "succeeded",
                "failed", "phase", "reason", "observedGeneration"):
        if key in status:
            result[key] = status[key]
    if resource == "events":
        owner = item.get("involvedObject", {})
        return {"kind": owner.get("kind"), "object": owner.get("name"),
                "reason": item.get("reason"), "count": item.get("count", 1)}
    if conditions := status.get("conditions"):
        result["conditions"] = [{key: condition.get(key) for key in ("type", "status", "reason")}
                                for condition in conditions]
    if resource == "services":
        result["type"] = item["spec"].get("type")
    if resource == "pods":
        result["containers"] = [
            {"name": container["name"], "ready": container.get("ready"),
             "restarts": container.get("restartCount"), "image": container.get("image"),
             "imageId": container.get("imageID"),
             "state": {name: {key: state[key] for key in ("reason", "exitCode") if key in state}
                       for name, state in container.get("state", {}).items()}}
            for container in status.get("initContainerStatuses", []) + status.get("containerStatuses", [])
        ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--context")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summary(args.namespace, args.context), indent=2) + "\n")


if __name__ == "__main__":
    main()