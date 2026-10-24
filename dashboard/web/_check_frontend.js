/*
 * FARM 看板前端自检脚本
 * 用法： node web/_check_frontend.js
 * 全部断言通过时打印 ALL_PASS；任一失败时打印 FAILED 并以退出码 1 结束。
 *
 * 检查项对应 CONTRACT.md §6：
 *  - <script> 抽出后用 vm.Script 验证语法
 *  - 纯原生 ES5（无模板字符串 / let / const / 箭头函数 / class / async-await / 可选链）
 *  - 零外网请求（无 http(s):// 外链、无 CDN、无 <link>、无 <script src>、无 @import）
 *  - esc() 存在且实现 5 类转义，接口文本必须经 esc() 再 innerHTML
 *  - 每个 data-act 都有 ACTIONS 处理器分支，且处理器函数真实定义
 *  - localStorage 键全部 farm_* 前缀
 *  - CSS 花括号配平、无 @import
 *  - 10 个界面齐全、图表用内联 SVG 手绘、401 清理三键
 */
'use strict';

var fs = require('fs');
var path = require('path');
var vm = require('vm');

var PASS = 0;
var FAIL = 0;
var MSGS = [];

function ok(name, extra) {
  PASS += 1;
  MSGS.push('PASS  ' + name + (extra ? '  (' + extra + ')' : ''));
}

function bad(name, detail) {
  FAIL += 1;
  MSGS.push('FAIL  ' + name + (detail ? '  → ' + detail : ''));
}

function check(name, cond, detail) {
  if (cond) { ok(name, detail); } else { bad(name, detail); }
}

var file = path.join(__dirname, 'index.html');
var html = '';
try {
  html = fs.readFileSync(file, 'utf8');
} catch (e) {
  bad('读取 index.html', String(e && e.message));
  console.log(MSGS.join('\n'));
  console.log('FAILED');
  process.exit(1);
}

var SIZE = Buffer.byteLength(html, 'utf8');
var LINES = html.split(/\r?\n/).length;

/* ---------- 0. 文件基本形态 ---------- */
check('index.html 存在且非空', SIZE > 8000, SIZE + ' 字节 / ' + LINES + ' 行');
check('文件以 <!DOCTYPE html> 开头', /^\s*<!DOCTYPE html>/i.test(html));
check('声明 UTF-8 编码', /<meta\s+charset=["']?utf-8/i.test(html));
check('含中文界面标题', html.indexOf('FARM 灌溉监控看板') >= 0);

/* ---------- 1. 抽取内联脚本 ---------- */
var scriptTags = html.match(/<script[^>]*>[\s\S]*?<\/script>/gi) || [];
check('存在内联 <script> 块', scriptTags.length > 0, scriptTags.length + ' 块');
check('只有一个内联脚本块', scriptTags.length === 1, scriptTags.length + ' 块');
check('没有 <script src=...> 外链脚本',
  !/<script[^>]+\bsrc\s*=/i.test(html));

var srcMatch = html.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
var JS = srcMatch ? srcMatch[1] : '';
check('成功抽取出 JS 正文', JS.length > 3000, JS.length + ' 字符');

/* ---------- 2. 语法编译 ---------- */
if (JS) {
  try {
    vm.createScript(JS, 'farm-web-inline.js');
    ok('vm.Script() 语法编译通过');
  } catch (e) {
    bad('vm.Script() 语法编译通过', String(e && e.message));
  }
} else {
  bad('vm.Script() 语法编译通过', '无脚本内容');
}

/* ---------- 3. ES5 纯度 ---------- */
check('无模板字符串（反引号）', JS.indexOf('`') < 0,
  JS.indexOf('`') < 0 ? '' : '仍有反引号');
check('无模板字符串插值 ${', JS.indexOf('${') < 0);
check('无 let 声明', !/\blet\b/.test(JS), (JS.match(/\blet\b/g) || []).length + ' 处');
check('无 const 声明', !/\bconst\b/.test(JS), (JS.match(/\bconst\b/g) || []).length + ' 处');
check('无箭头函数 =>', JS.indexOf('=>') < 0, (JS.match(/=>/g) || []).length + ' 处');
check('无 async 关键字', !/\basync\b/.test(JS), (JS.match(/\basync\b/g) || []).length + ' 处');
check('无 await 关键字', !/\bawait\b/.test(JS), (JS.match(/\bawait\b/g) || []).length + ' 处');
check('无可选链 ?.', !/\?\./.test(JS), (JS.match(/\?\./g) || []).length + ' 处');
check('无 class 声明/表达式', !/\bclass\s+[A-Za-z_$]/.test(JS));
check('无 ES6 解构赋值', !/\bvar\s*[[{]/.test(JS));
check('无 for...of 循环', !/\bfor\s*\([^)]*\bof\b/.test(JS));
check('无 Promise / fetch 现代 API',
  !/\bPromise\b/.test(JS) && !/\bfetch\s*\(/.test(JS));
check('无 eval / new Function',
  !/\beval\s*\(/.test(JS) && !/new\s+Function\s*\(/.test(JS));
check('无 document.write', !/document\.write/.test(JS));
check('脚本整体包在 IIFE 里', /^\s*\(function\s*\(/.test(JS) || /\(function \(\) \{/.test(JS));
check("含 'use strict'", JS.indexOf("'use strict'") >= 0);

/* ---------- 4. 零外网请求 ---------- */
check('无 http:// 外链', html.indexOf('http://') < 0);
check('无 https:// 外链', html.indexOf('https://') < 0);
check('无 //cdn 引用', !/\/\/[a-z0-9.-]*cdn/i.test(html));
check('无 unpkg / jsdelivr / googleapis / bootcdn',
  !/unpkg|jsdelivr|googleapis|bootcdn|cdnjs/i.test(html));
check('无 <link> 标签（外链样式/字体）', !/<link\b/i.test(html));
check('无 @import', !/@import/i.test(html));
check('无外部 <img src=...>', !/<img\b[^>]*\bsrc\s*=/i.test(html));
check('含内联 <style> 且非空',
  /<style[^>]*>[\s\S]{500,}<\/style>/i.test(html));
check('无第三方图表库引用',
  !/chart\.js|echarts|highcharts|d3\.js|plotly/i.test(html));
check('图标/装饰全部内联（无 iconfont）', !/iconfont|font-awesome/i.test(html));

/* ---------- 5. esc() 转义 ---------- */
check('存在 esc() 函数定义', /function\s+esc\s*\(/.test(JS));
check('esc 转义 &', /replace\(\/&\/g/.test(JS) || JS.indexOf('/&/g') >= 0);
check('esc 转义 <', JS.indexOf('/</g') >= 0);
check('esc 转义 >', JS.indexOf('/>/g') >= 0);
check('esc 转义 双引号', JS.indexOf('/"/g') >= 0);
check('esc 转义 单引号', JS.indexOf("/'/g") >= 0 || JS.indexOf('/\'/g') >= 0);

var escUses = (JS.match(/esc\(/g) || []).length;
check('esc() 被大量使用（接口文本全部转义）', escUses >= 60, escUses + ' 次调用');

/* 含 API 字段且写 innerHTML 的行，必须带 esc( */
var API_FIELD = /\b(?:d|me|w|f|u|a|o|it|cfg)\.(?:message|algorithm_info|city|last_error|weather|username|note|rule|detail)\b/;
var badLines = [];
JS.split(/\r?\n/).forEach(function (ln, idx) {
  if (ln.indexOf('innerHTML') >= 0 && API_FIELD.test(ln) && ln.indexOf('esc(') < 0) {
    badLines.push((idx + 1) + ': ' + ln.trim());
  }
});
check('innerHTML 中的接口字段都过 esc()', badLines.length === 0,
  badLines.length ? badLines.join(' | ') : '已检视全部 innerHTML 行');

/* 用户输入不得拼进 innerHTML：错误文案统一由 showErr() 内部转义 */
check('showErr() 内部对文案做 esc() 后写 innerHTML',
  /function\s+showErr\([\s\S]{0,320}?innerHTML\s*=\s*esc\(msg\)/.test(JS));
check('登录/改密错误均经 showErr 输出（不直接拼 innerHTML）',
  !/innerHTML\s*=\s*(?:d\.message|me\.message)/.test(JS));
check('API 文本用 textContent 的 toast 实现', /\.textContent\s*=\s*String\(msg\)/.test(JS));
check('confirm 弹窗标题/正文用 textContent', /modal-body'\)\.textContent/.test(JS) ||
  /'modal-title'\)\.textContent/.test(JS));

/* ---------- 6. data-act 与处理器 ---------- */
var actAttrs = {};
var reAct = /data-act=["']([A-Za-z0-9_-]+)["']/g;
var m;
while ((m = reAct.exec(html)) !== null) { actAttrs[m[1]] = true; }
var actNames = Object.keys(actAttrs).sort();
check('页面存在 data-act 属性', actNames.length > 0, actNames.length + ' 个：' + actNames.join(','));

var missing = [];
actNames.forEach(function (a) {
  var reKey = new RegExp('[\'"]' + a + '[\'"]\\s*:');
  if (!reKey.test(JS)) { missing.push(a); }
});
check('每个 data-act 都在 ACTIONS 表中有分支', missing.length === 0,
  missing.length ? '缺：' + missing.join(',') : '全部命中');

var ACT_BLOCK = JS.match(/var ACTIONS = \{([\s\S]*?)\n\s*\};/);
check('存在 ACTIONS 动作分发表', !!ACT_BLOCK);
if (ACT_BLOCK) {
  var body = ACT_BLOCK[1];
  var rePair = /['"]?([A-Za-z0-9_-]+)['"]?\s*:\s*([A-Za-z_$][\w$]*)/g;
  var pairs = [];
  var p;
  while ((p = rePair.exec(body)) !== null) { pairs.push([p[1], p[2]]); }
  check('ACTIONS 表项可解析', pairs.length >= 10, pairs.length + ' 项');
  var noFn = [];
  pairs.forEach(function (pr) {
    if (new RegExp('function\\s+' + pr[1] + '\\s*\\(').test(JS) === false) {
      noFn.push(pr[0] + '→' + pr[1]);
    }
  });
  check('每个动作都有真实定义的处理器函数', noFn.length === 0,
    noFn.length ? '缺函数：' + noFn.join(',') : pairs.length + ' 个处理器全部存在');
}

check('存在统一事件委托（click）', /addEventListener\('click'/.test(JS));
check('存在 change 委托（下拉改角色）', /addEventListener\('change'/.test(JS));
check('存在回车提交处理', /addEventListener\('keydown'/.test(JS));

/* ---------- 7. localStorage 键名 ---------- */
var lsKeys = {};
var reLs = /localStorage\.(?:getItem|setItem|removeItem)\(\s*'([^']+)'/g;
var k;
while ((k = reLs.exec(JS)) !== null) { lsKeys[k[1]] = true; }
var keyList = Object.keys(lsKeys).sort();
check('使用 localStorage 保存会话', keyList.length > 0, keyList.join(','));
var badKeys = keyList.filter(function (x) { return x.indexOf('farm_') !== 0; });
check('localStorage 键全部 farm_* 前缀', badKeys.length === 0,
  badKeys.length ? '违规：' + badKeys.join(',') : keyList.length + ' 个键全部合规');
['farm_token', 'farm_role', 'farm_user'].forEach(function (need) {
  check('包含必需键 ' + need, lsKeys[need] === true);
});
var extraKeys = keyList.filter(function (x) {
  return ['farm_token', 'farm_role', 'farm_user', 'farm_must_change'].indexOf(x) < 0;
});
check('无契约之外的 localStorage 键', extraKeys.length === 0,
  extraKeys.length ? extraKeys.join(',') : '仅 token/role/user/must_change');

/* 变量插值式键名一律禁止（防止动态键绕过前缀约束） */
check('localStorage 键名均为字面量', !/localStorage\.setItem\(\s*[A-Za-z_$]/.test(JS));

/* ---------- 8. 401 处理 ---------- */
check('存在 401 统一处理函数', /function\s+handle401\s*\(/.test(JS));
check('401 时清 token', /removeItem\('farm_token'\)/.test(JS));
check('401 时清 role', /removeItem\('farm_role'\)/.test(JS));
check('401 时清 user', /removeItem\('farm_user'\)/.test(JS));
check('401 时回登录门', /handle401[\s\S]{0,200}showGate\(\)/.test(JS));
check('请求 401 状态码被识别', /status\s*===\s*401/.test(JS));

/* ---------- 9. CSS ---------- */
var cssMatch = html.match(/<style[^>]*>([\s\S]*?)<\/style>/i);
var CSS = cssMatch ? cssMatch[1] : '';
check('抽出内联 CSS', CSS.length > 500, CSS.length + ' 字符');
var open = (CSS.match(/\{/g) || []).length;
var close = (CSS.match(/\}/g) || []).length;
check('CSS 花括号配平', open === close, '{ ' + open + ' / } ' + close);
check('CSS 变量定义主色 #2c7fb8', /--main:\s*#2c7fb8/i.test(CSS));
check('CSS 变量定义浅灰底 #f5f7fa', /--bg:\s*#f5f7fa/i.test(CSS));
check('圆角 10px', /--radius:\s*10px/.test(CSS));
check('正文 14px', /font-size:\s*14px/.test(CSS));
check('数字使用等宽字体', /\.mono\s*\{[^}]*monospace/.test(CSS));
check('存在响应式断点', /@media/.test(CSS));

/* ---------- 10. 10 个界面齐全 ---------- */
check('登录门 #gate', /id="gate"/.test(html));
check('强制改密门 #chpw', /id="chpw"/.test(html));
check('主界面 #app', /id="app"/.test(html));
check('顶栏导航 #nav', /id="nav"/.test(html));
check('错误提示 #toasts', /id="toasts"/.test(html));
check('二次确认弹窗 #modal', /id="modal"/.test(html));

/* 导航项来自 NAVS 数组（nav 按钮是运行时拼出来的，HTML 里没有静态 data-view） */
var navViews = {};
var NAV_BLOCK = JS.match(/var NAVS = \[([\s\S]*?)\n\s*\];/);
check('存在 NAVS 导航定义', !!NAV_BLOCK);
if (NAV_BLOCK) {
  var reId = /id:\s*'([a-z]+)'/g;
  var mi;
  while ((mi = reId.exec(NAV_BLOCK[1])) !== null) { navViews[mi[1]] = true; }
}
var viewKeys = Object.keys(navViews).sort();

/* 注意：脚本顶部有 `var VIEWS = {};` 占位声明，必须锚定到真正的注册块 */
var VIEW_BLOCK = JS.match(/VIEWS\s*=\s*\{\s*\n([\s\S]*?)\n\s*\};/);
check('存在 VIEWS 视图注册表', !!VIEW_BLOCK);
var regViews = {};
if (VIEW_BLOCK) {
  var reReg = /([a-z]+)\s*:\s*([A-Za-z_$][\w$]*)/g;
  var rv;
  while ((rv = reReg.exec(VIEW_BLOCK[1])) !== null) { regViews[rv[1]] = rv[2]; }
}
var regKeys = Object.keys(regViews).sort();
check('注册 9 个主界面视图（+登录门=10 个界面）', regKeys.length === 9,
  regKeys.join(','));
check('NAVS 与 VIEWS 完全对应',
  viewKeys.length === regKeys.length &&
  viewKeys.every(function (v, i) { return v === regKeys[i]; }),
  'nav=' + viewKeys.join(',') + ' | views=' + regKeys.join(','));
var noImpl = [];
Object.keys(regViews).forEach(function (kk) {
  if (new RegExp('function\\s+' + regViews[kk] + '\\s*\\(').test(JS) === false) {
    noImpl.push(kk);
  }
});
check('每个视图都有实现函数', noImpl.length === 0, noImpl.join(',') || '全部存在');
check('go() 会拦截无权限视图', /canDo\(min\)/.test(JS));

/* ---------- 11. 端点覆盖（§4 路由表） ---------- */
var ENDPOINTS = [
  ['POST', '/api/login'],
  ['GET', '/api/me'],
  ['POST', '/api/logout'],
  ['POST', '/api/password'],
  ['GET', '/api/overview'],
  ['GET', '/api/history'],
  ['GET', '/api/prediction'],
  ['GET', '/api/events'],
  ['GET', '/api/alerts'],
  ['POST', '/api/control'],
  ['GET', '/api/users'],
  ['PATCH', '/api/users/'],
  ['GET', '/api/export.csv']
];
var missEp = [];
ENDPOINTS.forEach(function (ep) {
  if (JS.indexOf(ep[1]) < 0) { missEp.push(ep[0] + ' ' + ep[1]); }
});
check('契约 §4 端点全部被调用', missEp.length === 0,
  missEp.length ? '缺：' + missEp.join(',') : ENDPOINTS.length + ' 个端点齐全');
check('history 带 hours 参数', /\/api\/history\?hours=/.test(JS));
check('history 带 points 参数', /points=200/.test(JS));
check('prediction 带 minutes 参数', /\/api\/prediction\?minutes=/.test(JS));
check('alerts 带 limit 参数', /\/api\/alerts\?limit=/.test(JS));
check('events 带 days 参数', /\/api\/events\?days=/.test(JS));
check('控制请求体含 action/channel/duration_minutes',
  /action:\s*action/.test(JS) && /channel:\s*v\.channel/.test(JS) &&
  /duration_minutes:\s*v\.minutes/.test(JS));
check('Authorization: Bearer 头', /Bearer\s*'/.test(JS));
check('导出 CSV 用 blob 下载', /createObjectURL/.test(JS));

/* ---------- 12. 图表：内联 SVG 手绘 ---------- */
check('折线图函数 chartLine', /function\s+chartLine\s*\(/.test(JS));
check('柱状图函数 chartBars', /function\s+chartBars\s*\(/.test(JS));
check('泵状态阶梯带 chartPump', /function\s+chartPump\s*\(/.test(JS));
check('环形进度 ring', /function\s+ring\s*\(/.test(JS));
check('SVG 节点手写生成（无第三方库）', /<svg\b/.test(JS));
check('使用 viewBox 自适应', /viewBox="0 0 /.test(JS));
check('图表宽度 100%', /\.chart\s*\{[^}]*width:\s*100%/.test(CSS));
check('图表颜色用 CSS 变量', /var\(--c1\)/.test(JS) || /CH_COLOR/.test(JS));
check('悬停提示用 <title>', /<title>/.test(JS));
check('折线用 polyline', /<polyline/.test(JS));
check('柱状用 rect', /<rect/.test(JS));
check('趋势支持 1/6/24/72/168 小时',
  JS.indexOf("'1', '1 小时'") >= 0 && JS.indexOf('168') >= 0);
check('预测支持 30/60/120 分钟',
  JS.indexOf("'30'") >= 0 && JS.indexOf("'120'") >= 0);
check('事件按天汇总柱状图', /按天汇总/.test(JS));

/* ---------- 13. 业务规则细节 ---------- */
check('数据过期阈值 600 秒并显示「数据已过期」',
  /data_age_sec/.test(JS) && /数据已过期/.test(JS) && />\s*600/.test(JS));
check('含水量状态色分级 30 / 95',
  /<\s*30/.test(JS) && />\s*95/.test(JS));
check('告警级别色块 critical/warning/info',
  /critical/.test(JS) && /warning/.test(JS) && /info/.test(JS));
check('告警未恢复置顶', /open\.concat\(done\)/.test(JS) || /未恢复置顶/.test(JS));
check('控制界面按角色隐藏（operator/admin）',
  /min:\s*'operator'/.test(JS));
check('账号界面仅 admin 可见', /min:\s*'admin'/.test(JS));
check('must_change 强制改密', /must_change/.test(JS) && /farm_must_change/.test(JS));
check('角色能力表 viewer/operator/admin',
  /viewer:\s*1/.test(JS) && /operator:\s*2/.test(JS) && /admin:\s*3/.test(JS));
check('预测精度按 0-1 小数换算百分比', /accuracy_by_channel/.test(JS) && /\*\s*100/.test(JS));
check('精度 0.0 照实展示不美化',
  /尚未收敛/.test(JS));
check('上游不可达提示', /upstream_ok/.test(JS));
check('二次确认后才下发控制', /openConfirm\('确认开始灌溉'/.test(JS));
check('操作后 toast 并刷新总览',
  /toast\('指令已下发/.test(JS) && /\/api\/overview'/.test(JS));
check('含水/时长保留 1 位小数', /toFixed\(1\)/.test(JS));

/* ---------- 14. 样式与结构整洁 ---------- */
check('HTML 标签 <html>/<head>/<body> 齐全',
  /<html[\s>]/i.test(html) && /<head[\s>]/i.test(html) && /<body[\s>]/i.test(html));
check('viewport meta 存在', /name="viewport"/.test(html));
var divOpen = (html.match(/<div\b/g) || []).length;
var divClose = (html.match(/<\/div>/g) || []).length;
check('静态 <div> 数量配平', divOpen === divClose,
  '<div ' + divOpen + ' / </div> ' + divClose);
check('无 TODO / FIXME 遗留', !/TODO|FIXME|XXX/.test(JS));

/* ---------- 输出 ---------- */
console.log('');
console.log('================ FARM 前端自检 ================');
console.log('目标文件：' + file);
console.log('体积：' + SIZE + ' 字节 / ' + LINES + ' 行 / JS ' + JS.length + ' 字符');
console.log('------------------------------------------------');
MSGS.forEach(function (s) { console.log(s); });
console.log('------------------------------------------------');
console.log('通过 ' + PASS + ' 条，失败 ' + FAIL + ' 条，共 ' + (PASS + FAIL) + ' 条断言');
if (FAIL === 0) {
  console.log('ALL_PASS');
  process.exit(0);
} else {
  console.log('FAILED');
  process.exit(1);
}
