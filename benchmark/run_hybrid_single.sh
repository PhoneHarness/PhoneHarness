#!/bin/bash
# 逐题跑 4app_new，每题拉 nested trace + 截图到本地
# Usage: bash benchmark/run_hybrid_single.sh [task_id ...]
# 无参数则跑全部 26 题

set -e
SERIAL="emulator-5556"
SHEET="4app_new"
TIMEOUT=300

# 要跑的题目列表
if [ $# -gt 0 ]; then
    TASKS="$@"
else
    TASKS="SC11_002 SC09_001 SC09_005 SC09_003 SC09_002 SC04_006 SC04_012 SC04_015 SC04_007 SC04_004 SC04_009 SC06_005 SC06_003 SC10_010 SC10_001 SC01_008 SC02_012 SC02_018 SC02_022 SC02_009 SC08_001 SC08_005 SC08_006 SC03_002 SC03_009 SC03_011"
fi

TOTAL=$(echo $TASKS | wc -w | tr -d ' ')
IDX=0

for TID in $TASKS; do
    IDX=$((IDX + 1))
    echo ""
    echo "============================================"
    echo "[$IDX/$TOTAL] $TID"
    echo "============================================"

    # 跑任务
    python3 benchmark/run_hybrid_bench.py --sheet $SHEET --task $TID --timeout $TIMEOUT

    # 找最新 run 目录
    RUN=$(ls -d benchmark/traces/hybrid_bench/$SHEET/2026* | sort | tail -1)

    # 找 Termux 里最新的 nested trace（可能多个 seed_gui 调用）
    TRACES=$(adb -s $SERIAL shell "run-as com.termux sh -c 'ls -t /data/data/com.termux/files/home/artifacts/seed_gui_traces/*.ndjson 2>/dev/null'" | head -5 | tr -d '\r')

    TRACE_IDX=0
    for REMOTE_TRACE in $TRACES; do
        # 检查是否属于这次运行（通过文件时间判断，粗略）
        TRACE_IDX=$((TRACE_IDX + 1))
        if [ $TRACE_IDX -le 3 ]; then
            BASENAME=$(basename "$REMOTE_TRACE" .ndjson)
            adb -s $SERIAL shell "run-as com.termux cat '$REMOTE_TRACE'" > "$RUN/${TID}.nested_${TRACE_IDX}.ndjson" 2>/dev/null
            echo "  saved nested trace $TRACE_IDX: $BASENAME"

            # 找对应截图目录
            SDIR=$(echo "$REMOTE_TRACE" | sed 's/.ndjson/_screenshots/')
            HAS_SS=$(adb -s $SERIAL shell "run-as com.termux sh -c 'ls $SDIR/step_001.png 2>/dev/null && echo YES || echo NO'" | tr -d '\r')
            if [ "$HAS_SS" = "YES" ]; then
                SS_COUNT=$(adb -s $SERIAL shell "run-as com.termux sh -c 'ls $SDIR/step_*.png 2>/dev/null | wc -l'" | tr -d '\r ')
                SS_DIR="$RUN/${TID}_screenshots_${TRACE_IDX}"
                mkdir -p "$SS_DIR"
                for i in $(seq 1 $SS_COUNT); do
                    STEP=$(printf "step_%03d.png" $i)
                    adb -s $SERIAL shell "run-as com.termux cat '$SDIR/$STEP'" > "$SS_DIR/$STEP" 2>/dev/null
                done
                echo "  saved $SS_COUNT screenshots -> $SS_DIR"
            fi
        fi
    done

    # 打印结果
    if [ -f "$RUN/$TID.report.json" ]; then
        python3 -c "import json; d=json.load(open('$RUN/$TID.report.json')); print(f'  RESULT: {d[\"status\"]} blocker={d.get(\"blocker\",\"\")} elapsed={d[\"elapsed\"]}s')"
    fi

    echo "  trace dir: $RUN/"
done

echo ""
echo "============================================"
echo "ALL DONE: $TOTAL tasks"
echo "============================================"
