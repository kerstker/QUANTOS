# 控制器维护互斥

单元暂停检查不能阻止root手动启动新的Demo控制器。现有各周期子目录锁也不能
覆盖任务准备、独立请求监督，以及组合控制器finally阶段的结果发布。

新增demo_supervisor.controller_lease，锁定既有控制器根目录inode，不创建锁文件
或修改目录内容。正常工作持共享锁，maintenance=True持独占锁；非阻塞冲突返回
CONTROLLER_MAINTENANCE_BUSY。打开前沿用root私有目录校验，打开后及加锁后核对
目录inode，fd使用CLOEXEC并在异常退出时关闭。

任务准备和请求监督从读取配置之前开始持锁，覆盖原任务claim、子进程生命周期、
结果写入及异常清理。组合控制器覆盖其finally发布，周期目录锁另持共享根锁，
从而保护功能周期恢复、组合周期和状态发布。共享锁允许已有合法并行与嵌套调用；
不构成订单、签发、重试或额度授权，所有原门禁保留。

## 验证

三项反例在实现前失败。相关模块初轮43项通过；补独立子进程真实flock冲突与
组合控制器入口拒绝后，监督/任务/周期/组合及launcher/checkpoint联合58项通过。
日志位于/home/kerstker/quantos-dev-output/demo-maintenance-services-20260924的
controller-before.log、controller-after.log、controller-final.log。
测试仅用临时目录、模拟配置或不访问账户的锁子进程，没有发单或实际维护锁。

## 剩余边界

这是协作锁，不阻止未采用该协议的旧进程、人工安装/配置工具或任意root写入。
现行已部署c34版本未包含该协议；不能拿开发代码的独占锁冒充现场停写证明。
生产维护仍需固定版本验收和受控切换、排空旧进程、暂停只读采集/展示，并约束
同一窗口内的人工配置操作。生产归档入口尚未组合，当前无生产记录移动。
