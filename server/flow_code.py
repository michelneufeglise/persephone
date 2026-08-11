"""
Execute user-provided TypeScript code in a Node subprocess.

Wraps the user code in an async function, spawns `npx tsx` with a 30s timeout,
captures stdout/stderr, and parses the result from a sentinel line.
"""

import asyncio
import json
import tempfile
import logging
from pathlib import Path
from typing import Any

import paths

log = logging.getLogger("flow_code")


async def run_user_code(code: str, input_data: Any = None) -> dict[str, Any]:
    """
    Execute user TypeScript code in a Node subprocess.

    Args:
        code: TypeScript code to execute (must return a value)
        input_data: JSON-serializable input data (available as `input` variable)

    Returns:
        {
            "ok": bool,
            "output": <result>,  # only if ok=true
            "logs": str,  # stdout + stderr captured during execution
            "error": str | None  # error message if ok=false
        }
    """
    temp_dir = Path(tempfile.gettempdir()) / "persephone-flows"
    temp_dir.mkdir(parents=True, exist_ok=True)

    input_file = temp_dir / f"{id(asyncio.current_task())}_input.json"
    wrapper_file = temp_dir / f"{id(asyncio.current_task())}_wrapper.ts"

    try:
        # Write input JSON
        with open(input_file, "w") as f:
            json.dump(input_data, f)

        # Write TypeScript wrapper
        # The user code is embedded as the body of an async function.
        # They have `input` in scope and must `return` a value.
        wrapper_code = f'''
import fs from "fs";

const inputJson = fs.readFileSync("{input_file}", "utf-8");
const input = JSON.parse(inputJson);

async function userCode() {{
  {code}
}}

userCode()
  .then((result) => {{
    console.log("RESULT:" + JSON.stringify(result));
  }})
  .catch((error) => {{
    console.error("ERROR:" + (error.message || String(error)));
    process.exit(1);
  }});
'''

        with open(wrapper_file, "w") as f:
            f.write(wrapper_code)

        # Run tsx with 30s timeout
        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "tsx", str(wrapper_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            proc.kill()
            return {
                "ok": False,
                "output": None,
                "logs": "",
                "error": "Code execution timeout (>30s)",
            }

        stdout = stdout_bytes.decode("utf-8", errors="ignore")
        stderr = stderr_bytes.decode("utf-8", errors="ignore")
        logs = (stdout + stderr).strip()

        # Truncate logs to ~10KB
        if len(logs) > 10000:
            logs = logs[:10000] + "\n… (truncated)"

        # Parse result from stdout
        if proc.returncode != 0:
            # Look for ERROR: line
            for line in stdout.split("\n"):
                if line.startswith("ERROR:"):
                    return {
                        "ok": False,
                        "output": None,
                        "logs": logs,
                        "error": line[6:],
                    }
            return {
                "ok": False,
                "output": None,
                "logs": logs,
                "error": f"Non-zero exit: {proc.returncode}",
            }

        # Look for RESULT: line
        result = None
        for line in stdout.split("\n"):
            if line.startswith("RESULT:"):
                try:
                    result = json.loads(line[7:])
                except json.JSONDecodeError as e:
                    return {
                        "ok": False,
                        "output": None,
                        "logs": logs,
                        "error": f"Invalid JSON result: {e}",
                    }
                break

        if result is None:
            return {
                "ok": False,
                "output": None,
                "logs": logs,
                "error": "No result returned (no RESULT: line found)",
            }

        return {
            "ok": True,
            "output": result,
            "logs": logs,
            "error": None,
        }

    finally:
        # Clean up temp files
        try:
            input_file.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            wrapper_file.unlink(missing_ok=True)
        except Exception:
            pass
