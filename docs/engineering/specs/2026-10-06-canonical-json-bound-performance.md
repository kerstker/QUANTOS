# 登记 JSON 节点检查开销优化

基于9763be7f0696696fc66ee7f24fc21be42d64dd2a，仅原纯合同、对应测试和本记录。
原完整6b后端超时的统计案例中，canonical检查遍历10479296节点，并反复调用
require。保留原遍历及递归，成功分支就地判断，失败仍沿原require错误码。
明确逐类型分支保留原JSON类型白名单、字节/节点/深度/整数检查；没有缓存、
数据裁剪、上限变化、来源或风险豁免。JSON编码与哈希格式保持。

两项公开CPU回归先实际2 FAIL/12 deselected（0.54秒/driver0.709364172秒），
原10000个null/bool值五次CPU采样，中位数26.1323/25.7865倍原JSON编码。
同原身份CPU预算15倍，优化后14 PASS（1.16秒/driver1.363931604秒）。随后
原登记/运行/评价/身份六组156 PASS，16.40秒/driver16.607480753秒，3138
Python摘要7fbc4a08前后保持；新增节点/深度/4096位整数、NaN byte limit
分配前拒绝及原保守字节预算反例，不调整输入或阈值。

旧/新独立对照410个有效输入编码字节相同，31个其他边界案例接受/拒绝结果、
错误类型和文本相同；字节集摘要a374e18b7beabae999058347b1366668c017d76c12e61764b626102d0a53bfc1。
原统计future反例相同cProfile参数1 PASS，49.02秒/driver49.314026202秒；
父版58.8028秒/driver59.008498719秒，本版剖析49.1285秒。canonical累计
17.4773→8.6178秒，遍历节点仍10479296，require调用39245308→1869956，
总调用290512303→253136889。两批后台负载和并行检查不同，不保证整门耗时；
该结果是剖析案例的增量证据。原字节语义、计算、拒绝边界和预算保持。

全E9/F821、服务图20域52边零环与diff通过。两现有文件完整Ruff均13条原
样式诊断，父/当前位置和内容相同，无新增；原完整发行Ruff按canonical
配置另验，不把这批说成全门已绿。

## 完整交付仍未完成

原6b冻结验收handle4232已终结exit1，elapsed12384.264587469秒，源码Python
3135前后保持。canonical后端原3600秒TIMEOUT；live/trading/tools阶段FAIL，
有Gold definition semantics拒绝及一个research risk readiness断言。其他
阶段按原validation_report分别留存；不借其PASS授予本版发行资格。后续先
定位具体行为错误，再在最终同版完整门验收，原时间预算不提高。

Windows指定repair-command.ps1不存在，修复未执行；已交直接粘贴命令，
真实CheckOnly待回。独立来源/结果/时间与OOS、实际成本风险、现场全writer
恢复及获准Demo现金反馈仍按既定F1–F5/四组核验，五秒与实际24小时保持。
实际trust、经济资格、晋级和下单不变；无部署、服务切换或保护状态写入。

## 冻结与主工作区验证

冻结6c57f740556fbec656d349fb9d2c2e6d06cc7083，实际hook791文件零违规；精确
DGX基线1wgsaovn通过，仅环境未--test。冻结版原登记/实际Gold完整导出/
合成签名消费/冷库复读20 PASS，driver25.602685405秒，3138 Python摘要
7fbc4a08保持；未代替真实用户独立签名。

父版本两个文件与Main相同并备份后，仅两Python与本记录选择整合；Main
同六组156 PASS，driver16.828444164秒，8005 Python摘要72b6c95e前后保持。
其他Python、HEAD913325cc、index95b9d9f1及现有权限保持，不提交Main。

原6b完整门正式终态：32项所选检查30 PASS/1 FAIL/1 TIMEOUT，其他52项是
该报告所记录的未选检查，不作通过。执行栈出现1个risk readiness FAIL及
156个Gold定义SEMANTICS_CHANGED setup ERROR（13个模块），失败和旧原件
均保持。该Source原32项报告不是本版全门。具体原因定位指向first_scope
fixture在旧PARENT checkout上声明档案，却调用当前已改动的Gold语义模块；
定义工具拒绝两者不一致。这是待核对的诊断，不能先放宽guard。risk fixture
仅提供stress标签，现行九引擎guard要求九份原通过报告；需保留拒绝反例并
修正测试资格，不放宽实际guard。后续从这两个真实问题继续。
