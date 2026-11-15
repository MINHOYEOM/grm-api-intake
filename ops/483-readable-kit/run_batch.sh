#!/usr/bin/env bash
# 사용: bash run_batch.sh X-0006   — 3조각 동시: 첫 번역 + 수정 2회. 결과는 logs/<X>.summary
set -u
cd "$(dirname "$0")"
B="$1"
python split_parts.py "$B" 50 > /dev/null
for round in first fix1 fix2; do
  flag=""; [ "$round" != first ] && flag="--fix"
  for p in 1 2 3; do python run_inline.py "$B-p$p" $flag > "logs/$B-p$p.$round.res" 2>&1 & done
  wait
done
# kept_tokens 로 신고한 행은 다시 맡겨 OCR 오독을 바로잡게 한 뒤 수정 1회
for p in 1 2 3; do
  ids=$(python -c "import json;o=json.load(open('parts/$B-p$p.out.json',encoding='utf-8'));print(','.join(k for k,v in o.items() if isinstance(v,dict) and v.get('kept_tokens')))")
  [ -n "$ids" ] && python run_inline.py "$B-p$p" --redo="$ids" > "logs/$B-p$p.redo.res" 2>&1 &
done; wait
for p in 1 2 3; do python run_inline.py "$B-p$p" --fix > "logs/$B-p$p.fix3.res" 2>&1 & done; wait
{ for p in 1 2 3; do echo "p$p $(tail -1 logs/$B-p$p.first.res) | $(python check_part.py $B-p$p | tail -1)"; done
  grep -h "usage limit\|try again" logs/$B-p*.log 2>/dev/null | head -1; } > "logs/$B.summary"
cat "logs/$B.summary"
