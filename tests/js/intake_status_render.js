/* 수집 현황(web/assets/admin-ops.js) 시험 하네스 — argv[2]=admin-ops.js, argv[3]=JSON {fn, args} → 결과 JSON. */
const path = require("path");
const ops = require(path.resolve(process.argv[2]));
const req = JSON.parse(process.argv[3]);
process.stdout.write(JSON.stringify(ops[req.fn].apply(null, req.args)));
