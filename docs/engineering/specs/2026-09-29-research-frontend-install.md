# 研究前端不可变安装准备

## 安装辅助器冻结与隔离root安装完成

辅助器及其验证固定为b79466f1a4da16350906017e94ece5a323da4085，基于81c0，
六文件；独立51项、实际暂存hook通过且提交后工作树干净。它是单独辅助器审查
版本，不把其新增源码算入仍在运行的81c0完整验收。

实际a14fde4c/1048源码+前端统一入口已在固定ARM64容器内完成root首次安装与
第二次安装。真实离线wheel安装、pip check、源码/证据身份验证、前端合同和
文件树校验均执行；第二轮源码返回已有安装，106前端文件哈希保持一致。
网络none，根文件系统只读，交付输入只读挂载；/opt、/var/tmp与/tmp为容器临时
文件系统，无宿主安装或生产配置变更。未运行systemd或服务切换。

首次演练安装依赖后，cryptography原生库映射失败；独立复核/proc/mounts确认
容器/opt tmpfs含noexec。该失败记录保留，第二次仅把容器/opt改成exec后通过。
未更换依赖、降低源码检查或改变宿主挂载。首轮已终止并清理后才启动第二轮，
两次容器均已删除。证据：
`/home/kerstker/quantos-dev-output/research-install-helper-20260929-5812z7w4/root-validation.json`。

只读现场检查确认三项next服务实际属于systemd用户管理器，均active/running；
系统管理器同名查询不是这些服务。后续切换方案必须绑定--user管理器及新鲜
单元原件摘要，不能照搬system级操作。当前仍未切换，81c0第二轮ARM64构建在途。

## 统一源码与前端安装包生成器

新增build_research_install_bundle：核对原源码交付、验证克隆的发行证据/源码锚、
匹配前端合同及全部载荷哈希后，生成一份固定绑定的install-source-and-frontend.py。
原安装器模板按已有独立摘要逐字节保留为research_source_install_template.py；
只接受替换绑定与包位置后的精确模板，不因report自报installer摘要就执行未知代码。

操作员入口先要求root，将所有188份载荷（含源码/依赖、106前端文件、清单和
两个安装辅助器）复制并校验到root创建的私有目录，才运行原源码安装函数；
前端辅助器随后通过已安装版本的Python以-I启动，仅导入该受保护发行目录。
最终状态RESEARCH_RELEASE_INSTALLED_NOT_ACTIVATED；没有服务重载、启动或
签发配置变更。原源码已安装但前端失败时不切换现场服务；重跑沿原安装收据
和不可覆盖前端检查继续。尚未做root容器或现场安装演练。

已有a14fde4c/1048配对真实产物生成成功，188份输入已逐项核验；普通用户执行
生成脚本在任何暂存/安装前拒绝。未绑定入口、复制不执行payload及变更/软链
拒绝与前端组件联合7项通过。真实材料目录：
`/home/kerstker/quantos-dev-output/research-combined-install-20260929-cyyo1y12`。
此材料用于安装器开发复核，不建议安装旧版替代当前候选；81c0当前第二轮ARM64
构建在途，新安装辅助器未纳入该验收源码。后续必须冻结辅助器、完成root演练，
再绑定新候选最终产物并准备三服务切换/恢复方案。

新增 tools/install_research_frontend.py，与原源码/venv安装互补，不修改服务。
输入必须固定artifact_commit、source_anchor、manifest_sha256。沿原前端合同
核对同源真实API、mock关闭、构建/运行身份、精确文件集合及整树摘要，再复制
到独立暂存目录；复制字节逐项复核并fsync。发布采用同文件系统rename，已有
版本只接受完全匹配且归属正确的目录，不覆盖原版本。失败暂存保留，不删除。

root安装入口固定/opt/quantos/releases/<commit>及/opt/quantos/frontends，先
验证受保护已安装源码与原发行证据/源码锚，然后发布前端。没有systemctl调用、
服务切换、生产配置改写、凭证读取或订单功能。当前只有可组合安装函数，没有
生成绑定当前新候选的统一操作员入口；不能要求用户直接运行未绑定开发模块。

五项新增场景覆盖正常暂存发布、重复调用、内容/清单/版本变化及软链拒绝；联合
原打包器49项通过。初次新增测试证明入口不存在，实现后修正测试对原打包器
ReleasePackageError的异常类型预期，最终联合通过。没有删去失败用例。

实际已验收a14fde4c前端106文件已在用户开发输出目录完成复制、逐项验证、发布
和重复调用演练，原包不变。证据：
`/home/kerstker/quantos-dev-output/frontend-install-rehearsal-20260929-ussopv3r/summary.json`。
该演练不是root安装、现场切换或新版策略验收。

81c0e7db39ff214c8d2222f06916a66badb933e6完整验收独立运行，当前正在首轮ARM64
冷构建；本安装器是它之后的开发增量，不修改在途源码或借用其未来验收结论。
仍需把此辅助器纳入审查/冻结的操作员交付，绑定同一已验收源码与前端，完成
root安装演练及三服务排空/切换/回滚后，才可请求最终现场操作授权。
# 2026-09-29：旧runner排空的现场差异与交付修正

只读systemd元数据确认旧runner仍为9月14日accepted-source-3b1ed3941，
PID3262125，TimeoutStopUSec=45s、SendSIGKILL=yes、KillMode=control-group。
原_lifespan只取消调度协程，没有新版queue.join和runner_ownership，实际旧状态根
research-runner.lock不存在。因此不能把生命周期返回或锁不存在当作任务已排空。

隔离探针按文件摘要提取旧原函数和JobQueue类，使用同机Python3.12、合成串行队列
及正在执行的调度线程；只向探针主进程发SIGTERM。两场景均在_lifespan返回后继续
完成合成任务，约4.08秒后进程退出。证据位于
`/home/kerstker/quantos-dev-output/research-old-runner-drain-20260929-shhr6ykg/attempt-2`。
初次探针未提供原相对导入的包命名空间，调度场景超时，保留原失败；重试仅修正
探针命名空间。未导入真实app/凭证加载器，未测试完整ASGI或现场systemd停止。

新材料明确要求先对旧runner应用并读回TimeoutStopSec=infinity、SendSIGKILL=no、
KillMode=mixed，再请求停止并等待原PID及整个cgroup排空；不能仅增加停止超时，
因为control-group仍会向所有子进程发送SIGTERM。三项组合已通过systemd-analyze
--user verify语法验证，没有启动单元。尚需实现/核验实际切换与恢复入口。

238e已验收9c811479产物的联合安装包保持不变，旧三服务材料另标
next-service-delivery-cutover-review.json为NOT_READY_FOR_CUTOVER。尚未开始的b81
旧等待队列已记录明确撤下原因，新管道wtsw23tf以同一b81源码和修正renderer继续
完整验收/打包，不修改正在验收的源码或现场服务。新材料仍不代表部署批准。
# 研究三服务可恢复切换入口（2026-09-29开发增量）

2026-09-29续接：`tools/build_research_operator_bundle.py` 将原联合安装器及
上述切换入口打包为独立 `operate.py`。默认仅校验固定载荷摘要；显式apply
才依次调用一次sudo安装和普通用户三服务切换。安装失败不继续切换，重跑
复用原安装收据和切换操作目录。此为开发入口，尚未获得现场执行证据。

真实238e已验收产物的私有审查副本已完成打包和 `python -I operate.py`
独立只读运行。副本修正runner的KillMode=mixed及对应载荷摘要；原交付
文件未改。目录为
`/home/kerstker/quantos-dev-output/research-operator-package-20260929-2a8hyu90`。
不是最新b81交付，不应据此部署旧版本。联合15项检查通过；覆盖安装失败
阻断切换、默认无操作、载荷变更拒绝及原切换恢复。两次初始打包拒绝分别
发现workspace元数据和原联合包文件使用0664；前者仅作为固定摘要数据读取，
后者用原copy_checked逐字节复制为0600私有输入。未改变现场文件权限。

最新b81验收原进程仍存活；最终产物通过后仍需绑定本入口并验证实际安装与
systemd切换。独立经济准入、连续批准、真实策略Demo与资金反馈仍未完成。

`tools/research_service_cutover.py` 已实现绑定固定交付计划摘要的用户管理器入口。
默认只复核；显式 `--apply` 才执行。先核验已安装源码及匹配前端，再安装
runner排空设置，记录原PID启动标识和cgroup，等待旧任务和运行锁释放，
然后切换三个研究服务。启动顺序为console、runner、web；版本目录和本地
HTTP健康检查都通过后才记录完成。健康检查不跟随重定向。

操作阶段和原进程身份持久化；相同计划、相同operation目录可恢复部分文件
写入中断。保留原研究数据及workspace配置，不自动回退数据，不安装或修改
交易授权。此入口尚未与最终联合安装包组成一次操作的完整现场交付。

隔离文件系统及替身服务管理器验证覆盖旧任务排空、部分写入中断恢复、重复
完成、未知配置变化拒绝、原PID/cgroup残留与HTTP重定向拒绝。连同原前端
安装检查共8项通过，仓库卫生通过；三个单元载荷与当前b81交付管道的原
renderer逐字节一致。证据：
`/home/kerstker/quantos-dev-output/research-cutover-verification-20260929-79w2xuwp/summary.json`。
尚未证明实际systemd切换、未改变现场服务、未部署。b81完整验收仍在原
管道PID3777897/验收PID3780895/ARM64驱动PID3781112运行，未作成功结论。
