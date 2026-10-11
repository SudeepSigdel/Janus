# Job kinds for scripts\eval_worker.ps1 (re-read before every job). Each kind maps to FIXED
# commands; a job file only picks a kind and an output id. Arguments are separated by "|".
$ModelKinds = @("smoke", "dev", "test", "perturb", "perturb-orig", "miniwob", "miniwob-smoke", "wa-smoke", "wa-reddit", "miniwob-1", "wasp-smoke", "wasp-forum")

function JobCommands([string]$kind, [string]$id) {
    # Each command is ONE string with arguments separated by "|" (strings never get unrolled by
    # PowerShell's pipeline, unlike nested arrays). The caller splits them.
    $o = "$Out\$id.jsonl"
    $janus = "run|janus-bench|run|--agent|janus|--tasks|tasks"
    $an = "run|janus-bench|analyze|--tasks|tasks|--records|$o"
    switch ($kind) {
        "unit"         { return @("run|pytest|-q") }
        "browser"      { return @("run|pytest|-q|-m|browser and not ollama") }
        "ruff"         { return @("run|ruff|check|.", "run|ruff|format|--check|.") }
        "oracle"       { return @("run|janus-bench|run|--agent|oracle|--tasks|tasks|--out|$o") }
        "smoke"        { return @("$janus|--split|splits/smoke.yaml|--set|dev|--repeats|1|--trace|--out|$o", "$an|--split|splits/smoke.yaml|--set|dev") }
        "dev"          { return @("$janus|--split|splits/v2.yaml|--set|dev|--repeats|3|--trace|--out|$o", "$an|--split|splits/v2.yaml|--set|dev") }
        "test"         { return @("$janus|--split|splits/v2.yaml|--set|test|--checkpoint|CP4|--repeats|3|--out|$o", "$an|--split|splits/v2.yaml|--set|test") }
        "perturb"      { return @("$janus|--split|splits/perturb.yaml|--set|dev|--repeats|3|--perturbation|p1|--trace|--out|$o", "$an|--split|splits/perturb.yaml|--set|dev") }
        "perturb-orig" { return @("$janus|--split|splits/perturb.yaml|--set|dev|--repeats|3|--trace|--out|$o", "$an|--split|splits/perturb.yaml|--set|dev") }
        "sync"         { return @("sync|--all-extras") }
        "miniwob-smoke" { return @("run|janus-bench|miniwob|--agent|janus|--split|smoke|--repeats|1|--out|$o") }
        "miniwob"      { return @("run|janus-bench|miniwob|--agent|janus|--split|dev|--repeats|3|--out|$o") }
        "miniwob-1"    { return @("run|janus-bench|miniwob|--agent|janus|--split|dev|--repeats|1|--out|$o") }
        "miniwob-null" { return @("run|janus-bench|miniwob|--agent|null|--split|dev|--repeats|1|--out|$o") }
        # WebArena-Verified (docs/PLAN.md B3). Run dir = results\overnight\<id>. Needs Docker Desktop.
        "wa-smoke"     { return @("run|janus-bench|webarena|--site|reddit|--only|399,580,28|--out-dir|$Out\$id") }
        "wa-reddit"    { return @("run|janus-bench|webarena|--site|reddit|--split|reddit_hard|--out-dir|$Out\$id") }
        # Official deterministic evaluator, in its own uv tool env. Job id "<run id>-eval".
        "wa-eval"      { $run = $id -replace '-eval$', ''
                         return @("tool|run|webarena-verified|eval-tasks|--output-dir|$Out\$run|--config|$Out\$run\eval_config.json") }
        # WASP forum injection scenarios (docs/PLAN.md B4); needs external\wasp and Docker Desktop.
        "wasp-smoke"   { return @("run|janus-bench|wasp|--only|a0-ghpt-g0,a7-ghui-g1|--out|$o") }
        "wasp-forum"   { return @("run|janus-bench|wasp|--out|$o") }
        "wa-diag"      { return @("run|python|scripts/wa_diag.py") }
        "wa-diag-page" { return @("run|python|scripts/wa_diag_page.py") }
        "wa-diag-reg"  { return @("run|python|scripts/wa_diag_reg.py") }
        default        { return $null }
    }
}

