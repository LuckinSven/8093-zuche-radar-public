const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const toast=m=>{const el=$('#toast');el.textContent=m;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),2600)};
const api=async(url,options={})=>{const r=await fetch(url,options);const body=await r.json().catch(()=>({}));if(!r.ok)throw new Error(body.detail?.[0]?.msg||body.detail||'请求失败');return body};
const json=(method,body)=>({method,headers:{'content-type':'application/json'},body:JSON.stringify(body)});
const formBody=form=>Object.fromEntries([...new FormData(form)].filter(([,value])=>value!==''));
const local=d=>{const z=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${z(d.getMonth()+1)}-${z(d.getDate())}T${z(d.getHours())}:${z(d.getMinutes())}`};
function preset(name,form=document){const now=new Date(),day=now.getDay(),until=(6-day+7)%7||7;let add=until;if(name==='next_saturday')add+=7;const start=new Date(now);start.setDate(now.getDate()+add);start.setHours(9,0,0,0);const end=new Date(start);end.setDate(start.getDate()+(name==='this_weekend'?2:1));$('[name=pickup_time]',form).value=local(start);$('[name=return_time]',form).value=local(end);}
async function loadDiscovery(scanId){const f=new FormData($('#discovery-filters')),p=new URLSearchParams({scan_id:scanId});for(const[k,v]of f)if(v)p.set(k,v);const data=await api('/api/discovery?'+p),g=$('#group-filter'),selected=g.value;g.innerHTML='<option value="">全部原生分组</option>'+data.groups.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');g.value=selected;$('#result-count').textContent=data.items.length;$('#results').innerHTML=data.items.length?data.items.map(card).join(''):'<div class="empty-state"><b>没有匹配车型</b><p>放宽筛选条件再看看。</p></div>';}
function card(x){return `<article class="vehicle-card"><div class="card-top"><div>${x.native_groups.map(g=>`<span class="badge">${esc(g)}</span>`).join('')}</div><span class="badge">${x.offers.some(o=>o.bookable)?'可预订':'候补'}</span></div><h3>${esc(x.model_name)}</h3><p>${esc(x.model_desc||'神州暂未提供车型描述')}</p><div class="price">¥${esc(x.lowest_price??'—')} <small>基础/列表价</small></div><div class="meta">最近：${esc(x.nearest_department)} · ${esc(x.nearest_distance_km??'—')} km<br>${x.changes.map(c=>`<span class="badge">${esc(c)}</span>`).join('')}</div><a href="/models/${esc(x.model_id)}">查看车型档案 →</a></article>`}
async function initDiscovery(){
  const form=$('#manual-scan-form'),department=$('#scan-department'),district=$('#location-district'),metro=$('#location-metro'),filters=$('#discovery-filters'),filterDepartment=filters.elements.department_id;
  preset('this_saturday',form);
  const button=$('#manual-scan-submit'),label=$('#manual-scan-submit-label'),feedback=$('#scan-feedback');
  const setFeedback=(state,title,detail)=>{feedback.dataset.state=state;$('#scan-feedback-title').textContent=title;$('#scan-feedback-detail').textContent=detail};
  const updateDataLink=scanId=>{const params=new URLSearchParams(new FormData(filters));for(const[key,value]of [...params])if(!value)params.delete(key);if(scanId)params.set('scan_id',scanId);$('#view-data-table').href='/data'+(params.size?'?'+params:'')};
  const renderSummary=summary=>{$('#fish-model-count').textContent=summary.primary_model_count;$('#nearby-model-count').textContent=summary.supplemental_model_count;$('#new-model-count').textContent=summary.new_or_reappeared_count;$('#latest-scan-summary').textContent=`${new Date(summary.started_at).toLocaleString()} · ${new Date(summary.pickup_time).toLocaleString()} → ${new Date(summary.return_time).toLocaleString()}`;$('#scan-status').textContent='扫描完成';updateDataLink(summary.scan_id)};
  const loadSummary=async scanId=>renderSummary(await api(`/api/discovery/summary?scan_id=${encodeURIComponent(scanId)}&primary_department_id=79340`));
  department.onchange=()=>{const option=department.selectedOptions[0];$('#selected-department-name').textContent=option.textContent;filters.elements.department_id.value=option.value;updateDataLink(sessionStorage.getItem('lastScanId'))};
  const fish={zuche_dept_id:79340,name:'鱼珠地铁站服务点',district:'黄埔区',latitude:23.10161,longitude:113.432649};
  const station=item=>item.name.match(/(.+?地铁站)/)?.[1]||'其他网点';
  const loadLocations=async()=>{const data=await api('/api/departments?page_size=100'),items=[fish,...data.items.filter(item=>item.zuche_dept_id!==fish.zuche_dept_id&&item.latitude!==null&&item.longitude!==null)];
    const renderDepartments=()=>{const selectedStation=metro.value,candidates=items.filter(item=>(item.district||'区域未知')===district.value&&station(item)===selectedStation);department.innerHTML=candidates.map(item=>`<option value="${esc(item.zuche_dept_id)}" data-name="${esc(item.name)}" data-lat="${esc(item.latitude)}" data-lon="${esc(item.longitude)}">${esc(item.name)}</option>`).join('');department.onchange()};
    const renderMetros=()=>{const values=[...new Set(items.filter(item=>(item.district||'区域未知')===district.value).map(station))];metro.innerHTML=values.map(value=>`<option${value==='鱼珠地铁站'?' selected':''}>${esc(value)}</option>`).join('');renderDepartments()};
    const districts=[...new Set(items.map(item=>item.district||'区域未知'))];district.innerHTML=districts.map(value=>`<option${value==='黄埔区'?' selected':''}>${esc(value)}</option>`).join('');
    filterDepartment.innerHTML='<option value="79340">鱼珠地铁站服务点</option><option value="">鱼珠 + 附近网点</option>'+items.filter(item=>item.zuche_dept_id!==79340).map(item=>`<option value="${esc(item.zuche_dept_id)}">${esc(item.name)}</option>`).join('');filterDepartment.value='79340';district.onchange=renderMetros;metro.onchange=renderDepartments;renderMetros()};
  loadLocations().catch(()=>{});
  $$('[data-preset]',form).forEach(item=>item.onclick=()=>{$$('[data-preset]',form).forEach(x=>x.classList.remove('active'));item.classList.add('active');if(item.dataset.preset!=='custom')preset(item.dataset.preset,form)});
  filters.onchange=()=>updateDataLink(sessionStorage.getItem('lastScanId'));
  $('#reset-filters').onclick=()=>{filters.reset();updateDataLink(sessionStorage.getItem('lastScanId'))};
  let busy=false,timer=null;
  form.onsubmit=async event=>{event.preventDefault();if(busy)return;const option=department.selectedOptions[0],times=new FormData(form),body={city_id:'14',location_name:option.dataset.name||option.textContent,latitude:Number(option.dataset.lat),longitude:Number(option.dataset.lon),pickup_time:times.get('pickup_time'),return_time:times.get('return_time')};let elapsed=0,completed=false;busy=true;form.setAttribute('aria-busy','true');button.disabled=true;label.textContent='正在扫描（0 秒）';$('#scan-status').textContent='正在扫描…';setFeedback('running','扫描请求已开始','正在查询鱼珠及附近网点，请勿重复点击。');toast('扫描已开始，请稍候');timer=setInterval(()=>{elapsed+=1;label.textContent=`正在扫描（${elapsed} 秒）`;setFeedback('running','正在扫描',`已等待 ${elapsed} 秒，正在查询鱼珠及附近网点。`)},1000);try{const result=await api('/api/scans',json('POST',body));if(result.status!=='SUCCESS')throw new Error(result.error_message||'神州未返回可用扫描结果');completed=true;sessionStorage.setItem('lastScanId',result.scan_id);label.textContent='正在汇总结果…';await loadSummary(result.scan_id);setFeedback('success','扫描完成',`${result.department_count} 个网点 · ${result.offer_count} 条报价，详细数据已保存。`);toast('扫描完成，可到数据表查看详情')}catch(error){$('#scan-status').textContent=completed?'汇总失败':'扫描失败';setFeedback('failed',completed?'扫描完成，但汇总失败':'扫描失败',error.message);toast(error.message)}finally{clearInterval(timer);timer=null;busy=false;form.removeAttribute('aria-busy');button.disabled=false;label.textContent='立即扫描鱼珠附近车源'}};
  const last=sessionStorage.getItem('lastScanId');if(last)loadSummary(last).catch(()=>{});else updateDataLink(null);
}
async function initHistory(){const load=async()=>{const d=await api('/api/history');$('#history-list').innerHTML=d.items.length?d.items.map(x=>`<article class="history-item"><div><b class="${esc(x.status.toLowerCase())}">${esc(x.status)}</b><h3>${esc(x.location_name)}</h3><span>${new Date(x.started_at).toLocaleString()} · ${new Date(x.pickup_time).toLocaleString()} → ${new Date(x.return_time).toLocaleString()}</span></div><div><p>${esc(x.error_message||`${x.department_count} 网点 · ${x.offer_count} 报价 · ${x.model_count} 车型 · ${x.event_count} 项变化`)}</p>${x.events?.length?`<details><summary>展开变化</summary>${x.events.map(e=>`<p><span class="badge">${esc(e.type)}</span> ${esc(e.detail||'')}</p>`).join('')}</details>`:''}</div></article>`).join(''):'<div class="empty-state">暂无扫描历史</div>'};$('#refresh-history').onclick=load;load()}
async function initAdmin(){let cities={},probes={};const resetCity=()=>{const f=$('#city-form');f.reset();$('#city-edit-id').value='';$('#city-form-title').textContent='添加城市';$('#city-cancel').hidden=true},resetProbe=()=>{const f=$('#probe-form');f.reset();$('#probe-edit-id').value='';$('#probe-form-title').textContent='添加扫描点';$('#probe-cancel').hidden=true},load=async()=>{const[c,p]=await Promise.all([api('/api/cities'),api('/api/probes')]);cities=Object.fromEntries(c.items.map(x=>[x.id,x]));probes=Object.fromEntries(p.items.map(x=>[x.id,x]));const selected=$('#probe-city').value;$('#probe-city').innerHTML=c.items.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');if(selected)$('#probe-city').value=selected;$('#city-list').innerHTML=c.items.length?c.items.map(x=>`<article class="probe-item"><div><b>${esc(x.name)}</b><p>神州城市 ID：${esc(x.zuche_city_id)} · ${esc(x.latitude)}, ${esc(x.longitude)}</p></div><div><button class="text-button" data-city-edit="${esc(x.id)}">编辑</button> <button class="text-button danger" data-city-delete="${esc(x.id)}">删除</button></div></article>`).join(''):'<div class="empty-state">还没有城市</div>';$('#probe-list').innerHTML=p.items.length?p.items.map(x=>`<article class="probe-item"><div><b>${esc(x.name)}</b><p>${esc(cities[x.city_id]?.name||'未知城市')} · ${esc(x.latitude)}, ${esc(x.longitude)}</p></div><div><span class="badge">${x.enabled?'自动扫描开启':'已关闭'}</span><p>${esc(x.schedule||'无计划')}</p><button class="text-button" data-probe-edit="${esc(x.id)}">编辑</button> <button class="text-button" data-toggle="${esc(x.id)}" data-enabled="${esc(x.enabled)}" data-schedule="${esc(x.schedule||'')}">${x.enabled?'停用':'启用'}</button> <button class="text-button danger" data-delete="${esc(x.id)}">删除</button></div></article>`).join(''):'<div class="empty-state">还没有扫描点</div>'};$('#city-form').onsubmit=async e=>{e.preventDefault();const b=Object.fromEntries(new FormData(e.target));b.latitude=+b.latitude;b.longitude=+b.longitude;const id=$('#city-edit-id').value;try{await api(id?`/api/cities/${id}`:'/api/cities',json(id?'PATCH':'POST',b));toast(id?'城市已更新':'城市已保存');resetCity();load()}catch(x){toast(x.message)}};$('#probe-form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target),b=Object.fromEntries(f);b.city_id=+b.city_id;b.latitude=+b.latitude;b.longitude=+b.longitude;b.enabled=f.has('enabled');b.schedule=b.schedule||null;const id=$('#probe-edit-id').value;try{await api(id?`/api/probes/${id}`:'/api/probes',json(id?'PATCH':'POST',b));toast(id?'扫描点已更新':'扫描点已保存');resetProbe();load()}catch(x){toast(x.message)}};$('#city-list').onclick=async e=>{const edit=e.target.closest('[data-city-edit]'),remove=e.target.closest('[data-city-delete]');try{if(edit){const x=cities[edit.dataset.cityEdit],f=$('#city-form');$('#city-edit-id').value=x.id;f.elements.zuche_city_id.value=x.zuche_city_id;f.elements.name.value=x.name;f.elements.latitude.value=x.latitude;f.elements.longitude.value=x.longitude;$('#city-form-title').textContent='编辑城市';$('#city-cancel').hidden=false;f.scrollIntoView({behavior:'smooth'})}if(remove&&confirm('确定删除这个城市？城市下有扫描点时需先删除扫描点。')){await api(`/api/cities/${remove.dataset.cityDelete}`,{method:'DELETE'});toast('城市已删除');load()}}catch(x){toast(x.message)}};$('#probe-list').onclick=async e=>{const edit=e.target.closest('[data-probe-edit]'),toggle=e.target.closest('[data-toggle]'),remove=e.target.closest('[data-delete]');try{if(edit){const x=probes[edit.dataset.probeEdit],f=$('#probe-form');$('#probe-edit-id').value=x.id;f.elements.city_id.value=x.city_id;f.elements.name.value=x.name;f.elements.latitude.value=x.latitude;f.elements.longitude.value=x.longitude;f.elements.schedule.value=x.schedule||'';f.elements.enabled.checked=x.enabled;$('#probe-form-title').textContent='编辑扫描点';$('#probe-cancel').hidden=false;f.scrollIntoView({behavior:'smooth'})}if(toggle){if(toggle.dataset.enabled==='false'&&!toggle.dataset.schedule)throw new Error('请先编辑扫描点并设置计划');await api(`/api/probes/${toggle.dataset.toggle}`,json('PATCH',{enabled:toggle.dataset.enabled==='false'}));toast('扫描开关已更新');load()}if(remove&&confirm('确定删除这个扫描点？')){await api(`/api/probes/${remove.dataset.delete}`,{method:'DELETE'});toast('扫描点已删除');load()}}catch(x){toast(x.message)}};$('#city-cancel').onclick=resetCity;$('#probe-cancel').onclick=resetProbe;load()}
function bindEnergySubtype(form){
  const options={
    '燃油':['汽油','柴油','油电混动','其他','未知'],
    '新能源':['纯电','插电混动','增程','其他','未知'],
    '未知':['未知'],
  };
  const render=selected=>{const subtype=form.elements.energy_subtype,values=options[form.elements.energy_type.value]||[];subtype.replaceChildren(new Option(values.length?'请选择':'请先选择大类',''),...values.map(value=>new Option(value,value)));subtype.disabled=!values.length;if(values.includes(selected))subtype.value=selected;if(form.elements.energy_type.value==='未知')subtype.value='未知'};
  form.elements.energy_type.onchange=()=>render('');
  return (energyType,energySubtype)=>{form.elements.energy_type.value=energyType||'';render(energySubtype||'')};
}
async function initModel(){
  const id=document.body.dataset.modelId,energyForm=$('#manual-energy-form'),setEnergyForm=bindEnergySubtype(energyForm);
  const load=async()=>{const[d,a]=await Promise.all([api(`/api/models/${id}`),api(`/api/models/${id}/annotations`)]);$('#model-title').textContent=d.model_name;const s=d.source,p=d.personal,f=$('#personal-state-form'),energy=d.energy||{};f.elements.state.value=p.state;f.elements.note.value=p.note||'';f.elements.rented_on.value=p.rented_on?local(new Date(p.rented_on)):'';setEnergyForm(energy.type,energy.subtype);$('#source-summary').innerHTML=`<div class="price">¥${esc(s.lowest_price??'—')} <small>基础/列表价</small></div><p>${esc(s.model_desc||'神州暂未提供车型描述')}</p><p>最近网点：${esc(s.nearest_department||'—')} · ${esc(s.nearest_distance_km??'—')} km</p>`;$('#derived-summary').innerHTML=`<p><b>车身：</b>${esc(s.body_style||'未识别')}</p><p><b>座位：</b>${esc(s.seat_count||'未识别')}</p><p><b>神州描述推导：</b>${esc(s.energy_type||'未识别')}</p><p><b>车型库结论：</b>${esc(energy.type||'未知')}${energy.subtype&&energy.subtype!=='未知'?` · ${esc(energy.subtype)}`:''} · ${energy.source==='MANUAL'?'人工确认':esc(energy.source||'待补全')}</p>`;$('#offer-list').innerHTML=s.offers.length?s.offers.map(x=>`<article><div><b>${esc(x.department_name)}</b><p>${esc(x.distance_km??'—')} km</p></div><div><b>¥${esc(x.package_price??x.daily_price??'—')}</b><p>${x.bookable?'可预订':'暂不可订'}</p></div></article>`).join(''):'<div class="empty-state">最新扫描没有报价</div>';$('#price-history').innerHTML=d.history.length?d.history.map(x=>`<article><div><b>${new Date(x.started_at).toLocaleString()}</b><p>${esc(x.location_name)} · ${esc(x.department_name)}</p></div><div><b>¥${esc(x.lowest_price??'—')}</b><p>${x.bookable?'可预订':'暂不可订'}</p></div></article>`).join(''):'<div class="empty-state">暂无历史</div>';$('#annotation-list').innerHTML=a.items.length?a.items.map(x=>`<article><div><b>${esc(x.field_name)}</b><p>${esc(x.value)}</p></div><div><span class="badge">${esc(x.confidence)}</span><p>${esc(x.source)}${x.verified_at?' · '+esc(new Date(x.verified_at).toLocaleString()):''}</p></div></article>`).join(''):'<div class="empty-state">还没有人工补充</div>'};
  $('#personal-state-form').onsubmit=async e=>{e.preventDefault();try{await api(`/api/models/${id}/personal-state`,json('PUT',formBody(e.target)));toast('个人状态已保存');load()}catch(x){toast(x.message)}};
  energyForm.onsubmit=async e=>{e.preventDefault();try{await api(`/api/models/${id}/energy`,json('PUT',formBody(e.target)));toast('人工能源结论已保存');energyForm.elements.note.value='';load()}catch(x){toast(x.message)}};
  $('#annotation-form').onsubmit=async e=>{e.preventDefault();try{await api(`/api/models/${id}/annotations`,json('POST',formBody(e.target)));toast('补充信息已保存');e.target.reset();load()}catch(x){toast(x.message)}};
  load().catch(x=>toast(x.message));
}
async function initSettings(){
  const form=$('#baidu-settings-form'),testForm=$('#baidu-test-form'),status=$('#baidu-status'),secret=$('#baidu-secret-status'),result=$('#baidu-test-result');
  const buttons=()=>$$('button',form);
  const busy=active=>buttons().forEach(button=>button.disabled=active);
  const render=setting=>{
    form.elements.enabled.checked=setting.enabled;
    form.elements.default_region.value=setting.default_region;
    form.elements.test_keyword.value=setting.test_keyword;
    form.elements.ak.value='';
    secret.textContent=setting.masked_secret||'尚未配置';
    status.textContent=setting.enabled?'已启用':setting.configured?'已配置 · 未启用':'尚未配置';
    status.className=`integration-status ${setting.enabled?'ready':setting.configured?'paused':''}`;
  };
  const load=async()=>{const data=await api('/api/settings/integrations'),setting=data.items.find(item=>item.provider==='baidu_maps');if(!setting)throw new Error('百度地图配置读取失败');render(setting)};
  form.onsubmit=async event=>{
    event.preventDefault();busy(true);
    const data=new FormData(form),body={enabled:data.has('enabled'),ak:data.get('ak')||null,default_region:data.get('default_region'),test_keyword:data.get('test_keyword')};
    try{render(await api('/api/settings/integrations/baidu_maps',json('PUT',body)));toast('百度地图配置已保存')}
    catch(error){toast(error.message)}finally{busy(false)}
  };
  testForm.onsubmit=async event=>{
    event.preventDefault();
    busy(true);result.className='test-result running';result.innerHTML='<span>正在验证…</span><p>依次检查地点检索和坐标转换。</p>';
    try{
      const data=await api(testForm.action,{method:testForm.method.toUpperCase()}),place=data.place_suggestion,coordinate=data.coordinate_conversion;
      result.className='test-result success';
      result.innerHTML=`<span>验证成功 · ${esc(data.elapsed_ms)} ms</span><h4>${esc(place.name)}</h4><p>${esc(place.address||'未返回地址')}</p><dl><div><dt>百度 BD-09</dt><dd>${esc(place.bd09_latitude)}, ${esc(place.bd09_longitude)}</dd></div><div><dt>神州扫描 GCJ-02</dt><dd>${esc(coordinate.gcj02_latitude)}, ${esc(coordinate.gcj02_longitude)}</dd></div></dl>`;
      toast('百度地图 API 工作正常');
    }catch(error){result.className='test-result failed';result.innerHTML=`<span>验证失败</span><p>${esc(error.message)}</p>`;toast(error.message)}finally{busy(false)}
  };
  $('#clear-baidu-secret').onclick=async()=>{
    if(!confirm('确定清除已保存的百度地图 AK？清除后服务会同时停用。'))return;
    busy(true);try{await api('/api/settings/integrations/baidu_maps/secret',{method:'DELETE'});await load();result.className='test-result';result.innerHTML='<span>AK 已清除</span><p>重新填写并保存后才能测试。</p>';toast('百度地图 AK 已清除')}catch(error){toast(error.message)}finally{busy(false)}
  };
  try{await load()}catch(error){status.textContent='读取失败';toast(error.message)}
}
function initMapCache(){
  const form=$('#map-cache-filter'),list=$('#map-cache-list'),pages=$('#map-cache-pages');
  let currentPage=1;
  const time=value=>{if(!value)return '尚无记录';const date=new Date(value);return Number.isNaN(date.getTime())?'时间未知':date.toLocaleString()};
  const placeDetails=details=>Array.isArray(details)&&details.length
    ?details.map(place=>{const item=place&&typeof place==='object'?place:{};return `<div class="cache-place"><b>${esc(item.name||'未命名地点')}</b><p>${esc(item.address||'未返回地址')}</p><small>${esc(item.district||item.city||'地区未知')} · ${esc(item.latitude??'—')}, ${esc(item.longitude??'—')}</small></div>`}).join('')
    :'<p class="cache-muted">没有可展示的候选地点</p>';
  const coordinateDetails=details=>{const item=details&&typeof details==='object'?details:{};return `<dl class="cache-coordinate"><div><dt>源坐标 ${esc(item.source_crs||'—')}</dt><dd>${esc(item.source_latitude??'—')}, ${esc(item.source_longitude??'—')}</dd></div><div><dt>目标坐标 ${esc(item.target_crs||'—')}</dt><dd>${esc(item.target_latitude??'—')}, ${esc(item.target_longitude??'—')}</dd></div></dl>`};
  const renderItem=item=>`<article class="cache-item" data-kind="${esc(item.kind)}" data-id="${esc(item.id)}"><div class="cache-item-head"><div><span class="badge">${item.kind==='search'?'地点':'坐标'}</span><span class="badge">${esc(item.provider==='baidu_maps'?'百度地图':item.provider)}</span><h3>${item.kind==='search'?`${esc(item.region)} · ${esc(item.keyword)}`:esc(item.keyword)}</h3><p>命中 ${esc(item.hit_count)} 次 · 最近命中 ${esc(time(item.last_hit_at))} · 更新于 ${esc(time(item.refreshed_at))}</p></div><div class="cache-actions"><button class="text-button" type="button" data-cache-refresh>刷新</button><button class="text-button danger" type="button" data-cache-delete>删除</button></div></div><details><summary>查看详情</summary>${item.kind==='search'?placeDetails(item.details):coordinateDetails(item.details)}</details></article>`;
  const load=async(page=1)=>{
    currentPage=page;list.innerHTML='<div class="loading">正在读取地图缓存…</div>';
    try{
      const query=new URLSearchParams(new FormData(form));query.set('page',page);query.set('page_size','20');
      const data=await api('/api/settings/map-cache?'+query);
      $('#map-search-count').textContent=data.stats.search_count;
      $('#map-coordinate-count').textContent=data.stats.coordinate_count;
      $('#map-hit-count').textContent=data.stats.total_hit_count;
      $('#map-latest-refresh').textContent=time(data.stats.latest_refreshed_at);
      list.innerHTML=data.items.length?data.items.map(renderItem).join(''):'<div class="empty-state"><b>没有匹配的缓存</b><p>首次搜索地点后，会自动保存到这里。</p></div>';
      const pageData=data.pagination;currentPage=pageData.page;pages.innerHTML=pageData.pages>1?`<button class="text-button" type="button" data-page="${esc(pageData.page-1)}" ${pageData.page<=1?'disabled':''}>上一页</button><span>第 ${esc(pageData.page)} / ${esc(pageData.pages)} 页，共 ${esc(pageData.total)} 条</span><button class="text-button" type="button" data-page="${esc(pageData.page+1)}" ${pageData.page>=pageData.pages?'disabled':''}>下一页</button>`:'';
    }catch(error){list.innerHTML=`<div class="empty-state"><b>缓存读取失败</b><p>${esc(error.message)}</p></div>`;toast(error.message)}
  };
  form.onsubmit=event=>{event.preventDefault();load(1)};
  pages.onclick=event=>{const button=event.target.closest('[data-page]');if(button&&!button.disabled)load(Number(button.dataset.page))};
  list.onclick=async event=>{
    const article=event.target.closest('[data-kind][data-id]');if(!article)return;
    const kind=article.dataset.kind,id=Number(article.dataset.id);if(!['search','coordinate'].includes(kind)||!Number.isInteger(id))return;
    const refresh=event.target.closest('[data-cache-refresh]'),remove=event.target.closest('[data-cache-delete]');
    try{
      if(refresh){refresh.disabled=true;await api('/api/settings/map-cache/refresh',json('POST',{kind,id}));toast('地图缓存已刷新');await load(currentPage)}
      if(remove&&confirm('确定删除这条地图缓存？')){remove.disabled=true;await api('/api/settings/map-cache/item',json('DELETE',{kind,id}));toast('地图缓存已删除');await load(currentPage)}
    }catch(error){toast(error.message);if(refresh)refresh.disabled=false;if(remove)remove.disabled=false}
  };
  $('#clear-map-cache').onclick=async()=>{
    if(!confirm('确定清空全部地图缓存？清空后无法恢复。'))return;
    const confirmation=prompt('请输入“清空全部地图缓存”以继续：');
    if(confirmation===null){toast('已取消清理');return}
    if(confirmation!=='清空全部地图缓存'){toast('输入内容不一致，未清理任何缓存');return}
    try{await api('/api/settings/map-cache',json('DELETE',{confirmation}));toast('地图缓存已全部清空');await load(1)}catch(error){toast(error.message)}
  };
  load();
}
function initShenzhouCatalog(){
  const status=$('#shenzhou-catalog-status'),test=$('#shenzhou-catalog-test'),sync=$('#shenzhou-catalog-sync');
  const busy=value=>{test.disabled=value;sync.disabled=value};
  const render=data=>{$('#shenzhou-city-count').textContent=data.city_count;$('#shenzhou-active-count').textContent=data.active_count;$('#shenzhou-last-sync').textContent=data.last_synced_at?new Date(data.last_synced_at).toLocaleString():'尚未同步';status.textContent=data.last_synced_at?'目录已同步':'等待同步';status.className='integration-status '+(data.last_synced_at?'ready':'paused')};
  const load=async()=>{try{render(await api('/api/zuche/cities'))}catch(error){status.textContent='读取失败';status.className='integration-status paused';toast(error.message)}};
  test.onclick=async()=>{busy(true);try{const data=await api('/api/zuche/cities/test',json('POST',{}));toast(`匿名接口可用，发现 ${data.city_count} 个城市`)}catch(error){toast(error.message)}finally{busy(false)}};
  sync.onclick=async()=>{busy(true);try{const data=await api('/api/zuche/cities/sync',json('POST',{}));toast(`已同步 ${data.city_count} 个开放城市`);await load()}catch(error){toast(error.message)}finally{busy(false)}};
  load();
}
function initDataTable(){
  const form=$('#data-filter'),scanSelect=$('#data-scan'),departmentSelect=$('#data-department'),body=$('#data-table-body'),status=$('#data-status'),exportLink=$('#data-export'),initial=new URLSearchParams(location.search);
  let page=1,pages=0,sort='model_name',order='asc',busy=false,scans={};
  const value=(item,suffix='')=>item===null||item===undefined||item===''?'—':`${esc(item)}${suffix}`;
  const price=item=>item===null||item===undefined?'—':`¥${esc(item)}`;
  const query=()=>{const params=new URLSearchParams(new FormData(form));for(const[key,item]of [...params])if(!item)params.delete(key);params.set('page',String(page));params.set('sort',sort);params.set('order',order);return params};
  const updateExport=params=>{const exportParams=new URLSearchParams(params);exportParams.delete('page');exportLink.href='/api/data/export.csv?'+exportParams};
  const renderRow=item=>{
    const difference=item.shenzhou_distance_km!==null&&item.coordinate_distance_km!==null?Math.abs(item.shenzhou_distance_km-item.coordinate_distance_km):0;
    const distanceClass=difference>.3?' data-distance-warning':'';
    return `<tr><td>${esc(new Date(item.started_at).toLocaleString())}</td><td>${esc(item.scan_location)}</td><td><b>${esc(item.model_name)}</b><small>ID ${esc(item.model_id)}</small></td><td>${item.native_groups.length?item.native_groups.map(group=>`<span class="badge">${esc(group)}</span>`).join(''):'—'}</td><td>${value(item.energy_type)}</td><td>${value(item.body_style)} / ${value(item.seat_count,'座')}</td><td><b>${esc(item.department_name)}</b><small>ID ${esc(item.department_id)}</small></td><td class="data-address">${value(item.department_address)}</td><td${distanceClass}>${value(item.shenzhou_distance_km,' km')}</td><td${distanceClass}>${value(item.coordinate_distance_km,' km')}</td><td>${price(item.daily_price)}</td><td>${price(item.package_price)}</td><td><span class="badge ${item.bookable?'data-bookable':'data-unavailable'}">${item.bookable?'可租':'暂不可租'}</span></td></tr>`;
  };
  const render=payload=>{
    const selectedDepartment=departmentSelect.value||initial.get('department_id')||'';
    departmentSelect.innerHTML='<option value="">全部网点</option>'+payload.departments.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');departmentSelect.value=selectedDepartment;
    pages=payload.pages;$('#data-total').textContent=payload.total;status.textContent=payload.total?`共 ${payload.total} 行，当前显示第 ${payload.page} 页`:'当前扫描没有匹配数据';
    body.innerHTML=payload.items.length?payload.items.map(renderRow).join(''):'<tr><td colspan="13" class="empty-state">没有匹配的数据</td></tr>';
    $('#data-page-label').textContent=pages?`第 ${payload.page} / ${pages} 页`:'暂无分页';$('#data-prev').disabled=payload.page<=1;$('#data-next').disabled=!pages||payload.page>=pages;
    $$('[data-sort]',$('#data-table')).forEach(button=>{const active=button.dataset.sort===sort;button.dataset.active=active?'true':'false';button.dataset.order=active?order:''});
  };
  const load=async()=>{if(busy)return;busy=true;form.setAttribute('aria-busy','true');status.textContent='正在读取数据…';const params=query();updateExport(params);try{render(await api('/api/data/rows?'+params))}catch(error){status.textContent='数据读取失败';body.innerHTML=`<tr><td colspan="13" class="empty-state">${esc(error.message)}</td></tr>`;toast(error.message)}finally{busy=false;form.removeAttribute('aria-busy')}};
  const updateRentalPeriod=()=>{const item=scans[scanSelect.value];$('#data-rental-period').textContent=item?`${item.pickup_time_label} → ${item.return_time_label}`:'尚无扫描租期';$('#data-rental-location').textContent=item?`${item.location_name} · ${item.model_count} 个车型 · ${item.department_count} 个网点`:'请先完成一次扫描'};
  const loadScans=async()=>{const data=await api('/api/data/scans'),requested=initial.get('scan_id');scans=Object.fromEntries(data.items.map(item=>[item.id,item]));scanSelect.innerHTML=data.items.length?data.items.map((item,index)=>`<option value="${esc(item.id)}"${item.id===requested||(!requested&&index===0)?' selected':''}>${esc(new Date(item.started_at).toLocaleString())} · ${esc(item.location_name)} · ${esc(item.model_count)} 车型 / ${esc(item.department_count)} 网点</option>`).join(''):'<option value="">暂无成功扫描</option>';for(const name of ['q','energy_type','body_style','min_price','max_price','max_distance_km','seat_count','personal_state','change_type','bookable','department_id'])if(initial.has(name)&&form.elements[name])form.elements[name].value=initial.get(name);updateRentalPeriod()};
  form.onsubmit=event=>{event.preventDefault();page=1;load()};scanSelect.onchange=()=>{page=1;departmentSelect.value='';initial.delete('department_id');updateRentalPeriod();load()};
  $('#data-prev').onclick=()=>{if(page>1){page-=1;load()}};$('#data-next').onclick=()=>{if(page<pages){page+=1;load()}};
  $('#data-table').onclick=event=>{const button=event.target.closest('[data-sort]');if(!button)return;const next=button.dataset.sort;if(sort===next)order=order==='asc'?'desc':'asc';else{sort=next;order='asc'}page=1;load()};
  loadScans().then(load).catch(error=>{status.textContent='数据读取失败';toast(error.message)});
}
function initApiCatalog(){
  const button=$('#copy-ai-api-brief'),brief=$('#ai-api-brief');
  button.onclick=async()=>{try{await navigator.clipboard.writeText(brief.value);toast('接口说明已复制')}catch(_){brief.focus();brief.select();const copied=document.execCommand('copy');toast(copied?'接口说明已复制':'复制失败，请手动复制')}};
}
function initZucheUpstream(){
  const search=$('#upstream-search'),status=$('#upstream-status'),category=$('#upstream-category'),count=$('#upstream-visible-count'),items=$$('.upstream-item');
  const filter=()=>{const query=search.value.trim().toLowerCase();let visible=0;items.forEach(item=>{const show=(!query||item.dataset.search.toLowerCase().includes(query))&&(!status.value||item.dataset.status===status.value)&&(!category.value||item.dataset.category===category.value);item.hidden=!show;if(show)visible++});count.textContent=`${visible} / ${items.length}`};
  [search,status,category].forEach(control=>control.addEventListener(control===search?'input':'change',filter));
  $('#zuche-upstream-catalog').onclick=async event=>{const button=event.target.closest('[data-upstream-probe]');if(!button||button.disabled)return;const endpoint=button.dataset.upstreamProbe,result=$(`#probe-result-${endpoint}`);button.disabled=true;result.textContent='正在探测…';try{const data=await api(`/api/zuche/upstream/probe/${endpoint}`,json('POST',{}));result.textContent=`${data.summary} · ${new Date(data.checked_at).toLocaleString()}`;toast('安全探测完成')}catch(error){result.textContent=error.message;toast(error.message)}finally{button.disabled=false}};
}
function initDepartments(){
  const createForm=$('#department-discovery-form'),filterForm=$('#department-filter'),runList=$('#department-run-list'),departmentList=$('#department-list'),pages=$('#department-pages');
  let currentPage=1,refreshPromise=null,refreshBatch=null,pollTimer=null,disposed=false,actionBusy=false,guangzhouCityId=null;
  const displayTime=value=>{if(!value)return '尚无记录';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'时间未知':parsed.toLocaleString()};
  const dateValue=(base,days)=>{const value=new Date(base);value.setDate(value.getDate()+days);const pad=number=>String(number).padStart(2,'0');return `${value.getFullYear()}-${pad(value.getMonth()+1)}-${pad(value.getDate())}`};
  createForm.elements.pickup_date.value=dateValue(new Date(),1);
  createForm.elements.return_date.value=dateValue(new Date(),2);
  const cityQuery=()=>{const cityId=filterForm.elements.city_id.value;return cityId?`?city_id=${encodeURIComponent(cityId)}`:''};
  const renderSummary=item=>{
    $('#department-discovered-count').textContent=item.discovered_count;
    $('#department-coordinate-count').textContent=item.with_coordinates_count;
    $('#department-district-count').textContent=item.districts.length;
    $('#department-latest-seen').textContent=displayTime(item.latest_seen_at);
    $('#department-districts').innerHTML=item.districts.length?item.districts.map(district=>`<span class="badge">${esc(district.district)} · ${esc(district.count)}</span>`).join(''):'<span class="cache-muted">尚无行政区数据</span>';
  };
  const renderRun=item=>{
    const canStop=item.status==='RUNNING',canResume=['STOPPED','INTERRUPTED'].includes(item.status);
    const rounds=Array.isArray(item.rounds)&&item.rounds.length?`<details><summary>查看每轮新增趋势</summary>${item.rounds.map(round=>`<p>第 ${esc(round.round_number)} 轮 · ${esc(round.completed_point_count)} / ${esc(round.planned_point_count)} 点 · 新增 ${esc(round.new_department_count)}</p>`).join('')}</details>`:'';
    const error=item.last_error_summary?`<p class="department-error">安全错误：${esc(item.last_error_summary)}</p>`:'';
    return `<article class="department-run" data-run-id="${esc(item.id)}"><div class="department-run-head"><div><span class="badge">${esc(item.preset_label)}</span><span class="badge">${esc(item.status_label)}</span><h3>${esc(item.city_name||'当前城市')}发现任务</h3><p>${esc(displayTime(item.pickup_time))} → ${esc(displayTime(item.return_time))}</p></div><div class="department-run-actions">${canStop?'<button class="text-button danger" type="button" data-run-action="stop">停止</button>':''}${canResume?'<button class="text-button" type="button" data-run-action="resume">继续</button>':''}</div></div><div class="department-progress"><span><b>${esc(item.request_count)}</b>已预占请求</span><span><b>${esc(item.max_requests)}</b>请求上限</span><span><b>${esc(item.completed_point_count)} / ${esc(item.planned_point_count)}</b>处理点 / 计划点</span><span><b>${esc(item.new_department_count)}</b>新增网点</span></div>${error}${rounds}</article>`;
  };
  const renderRuns=data=>{runList.innerHTML=data.items.length?data.items.map(renderRun).join(''):'<div class="empty-state"><b>还没有发现任务</b><p>选择快速预设开始低频积累。</p></div>';if(actionBusy)$$('[data-run-action]',runList).forEach(button=>button.disabled=true)};
  const renderDepartment=item=>{
    const coordinate=item.latitude===null||item.longitude===null?'坐标待补充':`${esc(item.latitude)}, ${esc(item.longitude)}`;
    const hours=item.is_open_24h===true?'24 小时':esc(item.business_hours||'未返回营业时间');
    const selfService=item.self_service_pickup===true&&item.self_service_return===true?'支持自助取还':item.self_service_pickup===true?'支持自助取车':item.self_service_return===true?'支持自助还车':'未返回自助信息';
    return `<article class="department-item"><div><span class="badge">${esc(item.active_state_label)}</span><span class="badge">${esc(item.discovery_source_label)}</span><h3>${esc(item.name)}</h3><p>${esc(item.address||'未返回地址')}</p><small>${esc(item.district||'行政区未知')} · ${coordinate}</small></div><dl><div><dt>营业时间</dt><dd>${hours}</dd></div><div><dt>自助服务</dt><dd>${esc(selfService)}</dd></div><div><dt>首次发现</dt><dd>${esc(displayTime(item.first_seen_at))}</dd></div><div><dt>最近发现</dt><dd>${esc(displayTime(item.last_seen_at))}</dd></div><div><dt>来源原值</dt><dd>${esc(item.discovery_source)}</dd></div><div><dt>状态原值</dt><dd>${esc(item.active_state)}</dd></div></dl></article>`;
  };
  const renderDepartments=data=>{
    $('#department-list-total').textContent=`共 ${data.pagination.total} 条`;
    departmentList.innerHTML=data.items.length?data.items.map(renderDepartment).join(''):'<div class="empty-state"><b>没有匹配的已发现网点</b><p>调整筛选条件，或等待发现任务积累数据。</p></div>';
    const pagination=data.pagination;currentPage=pagination.page;pages.innerHTML=pagination.pages>1?`<button class="text-button" type="button" data-page="${esc(pagination.page-1)}" ${pagination.page<=1?'disabled':''}>上一页</button><span>第 ${esc(pagination.page)} / ${esc(pagination.pages)} 页，共 ${esc(pagination.total)} 条</span><button class="text-button" type="button" data-page="${esc(pagination.page+1)}" ${pagination.page>=pagination.pages?'disabled':''}>下一页</button>`:'';
  };
  const departmentQuery=()=>{const query=new URLSearchParams(new FormData(filterForm));for(const[key,value]of [...query])if(!value)query.delete(key);query.set('page',String(currentPage));query.set('page_size','20');return query};
  const reportRefreshError=(error,message)=>{const safeError=message?new Error(message):error instanceof Error?error:new Error('网点数据刷新失败，请重试');if(!safeError.departmentRefreshReported){safeError.departmentRefreshReported=true;toast(safeError.message)}return safeError};
  const refresh=()=>{
    if(disposed||document.hidden)return Promise.resolve();
    if(refreshPromise)return refreshPromise;
    const query=departmentQuery(),selectedCity=filterForm.elements.city_id.value,runQuery=new URLSearchParams({page:'1',page_size:'10'});if(selectedCity)runQuery.set('city_id',selectedCity);
    const controller=new AbortController(),batch={controller,abortReason:null},refreshTimeout=setTimeout(()=>{batch.abortReason='timeout';controller.abort()},8000),requestOptions={signal:controller.signal};refreshBatch=batch;
    const requests=[
      api('/api/departments/summary'+cityQuery(),requestOptions),
      api('/api/departments?'+query,requestOptions),
      api('/api/departments/discovery-runs?'+runQuery,requestOptions),
    ];
    refreshPromise=Promise.all(requests).then(([summary,departments,runs])=>{renderSummary(summary);renderDepartments(departments);renderRuns(runs)}).catch(error=>{if(error?.name!=='AbortError'){batch.abortReason='cascade';controller.abort();throw reportRefreshError(error,'网点数据刷新失败，请重试')}if(['operation','cascade'].includes(batch.abortReason))throw error;const message=batch.abortReason==='timeout'?'网点数据刷新超时，请重试':'网点数据刷新已取消，请重试';throw reportRefreshError(error,message)}).finally(async()=>{clearTimeout(refreshTimeout);await Promise.allSettled(requests);if(refreshBatch===batch)refreshBatch=null;refreshPromise=null});
    return refreshPromise;
  };
  const refreshAfterAction=async()=>{if(refreshPromise){if(refreshBatch){refreshBatch.abortReason='operation';refreshBatch.controller.abort()}await refreshPromise.catch(()=>{})}await refresh()};
  const loadCities=async()=>{
    const data=await api('/api/cities'),creationItems=data.items.filter(item=>item.enabled&&item.catalog_active),creationOptions=creationItems.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join(''),filterOptions=data.items.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');
    createForm.elements.city_id.innerHTML=creationOptions||'<option value="">尚无已启用开放城市</option>';
    filterForm.elements.city_id.innerHTML='<option value="">所有已记录城市</option>'+filterOptions;
    const guangzhou=creationItems.find(item=>item.zuche_city_id==='14');guangzhouCityId=guangzhou?.id??null;if(guangzhou){createForm.elements.city_id.value=String(guangzhou.id);filterForm.elements.city_id.value=String(guangzhou.id)}
  };
  const setActionBusy=value=>{actionBusy=value;$('button[type="submit"]',createForm).disabled=value;$('#department-city-sync').disabled=value;$('#department-directory-sync').disabled=value;$$('[data-run-action]',runList).forEach(button=>button.disabled=value)};
  $('#department-city-sync').onclick=async()=>{if(actionBusy)return;setActionBusy(true);try{const data=await api('/api/zuche/cities/sync',json('POST',{}));toast(`已同步 ${data.city_count} 个开放城市`);await loadCities();currentPage=1;await refreshAfterAction().catch(()=>{})}catch(error){toast(error.message)}finally{setActionBusy(false)}};
  $('#department-directory-sync').onclick=async()=>{if(actionBusy)return;if(!guangzhouCityId){toast('请先同步神州开放城市');return}const status=$('#department-create-status');setActionBusy(true);status.textContent='正在同步网点…';status.className='integration-status paused';try{const data=await api('/api/zuche/departments/sync',json('POST',{city_id:Number(guangzhouCityId)}));toast(`广州目录返回 ${data.department_count} 个网点，新增 ${data.new_count} 个`);status.textContent=`目录 ${data.department_count} 个网点`;status.className='integration-status ready';currentPage=1;filterForm.elements.city_id.value=String(guangzhouCityId);await refreshAfterAction().catch(()=>{})}catch(error){status.textContent='目录同步失败';toast(error.message)}finally{setActionBusy(false)}};
  createForm.onsubmit=async event=>{
    event.preventDefault();if(actionBusy)return;
    const form=new FormData(createForm),preset=form.get('preset');
    if(preset==='deep'&&!confirm('深度预设最多会发起 500 次请求，确定启动？'))return;
    const status=$('#department-create-status'),body={city_id:Number(form.get('city_id')),preset,pickup_date:form.get('pickup_date'),return_date:form.get('return_date')};if(preset==='deep')body.confirmation='确认启动深度发现';
    setActionBusy(true);status.textContent='正在创建…';status.className='integration-status paused';
    try{await api('/api/departments/discovery-runs',json('POST',body));toast('发现任务已创建');status.textContent='任务运行中';status.className='integration-status ready';await refreshAfterAction().catch(()=>{})}
    catch(error){status.textContent='创建失败';toast(error.message)}finally{setActionBusy(false)}
  };
  runList.onclick=async event=>{
    const button=event.target.closest('[data-run-action]'),article=event.target.closest('[data-run-id]');if(!button||!article||actionBusy)return;
    const operation=button.dataset.runAction;if(!['stop','resume'].includes(operation))return;
    setActionBusy(true);try{await api(`/api/departments/discovery-runs/${operation}`,json('POST',{run_id:article.dataset.runId}));toast(operation==='stop'?'发现任务已停止':'发现任务已继续');await refreshAfterAction().catch(()=>{})}catch(error){toast(error.message)}finally{setActionBusy(false)}
  };
  filterForm.onsubmit=event=>{event.preventDefault();currentPage=1;refreshAfterAction().catch(()=>{})};
  pages.onclick=event=>{const button=event.target.closest('[data-page]');if(button&&!button.disabled){currentPage=Number(button.dataset.page);refreshAfterAction().catch(()=>{})}};
  const startPolling=()=>{clearTimeout(pollTimer);if(disposed||document.hidden)return;pollTimer=setTimeout(async()=>{await refresh().catch(()=>{});startPolling()},3000)};
  document.addEventListener('visibilitychange',()=>{if(document.hidden){clearTimeout(pollTimer)}else{refresh().catch(()=>{}).finally(startPolling)}});
  window.addEventListener('pagehide',()=>{disposed=true;clearTimeout(pollTimer)},{once:true});
  loadCities().then(()=>refresh()).catch(error=>{if(!error?.departmentRefreshReported)toast(error.message)}).finally(startPolling);
}
function initDepartmentDirectory(){
  const filterForm=$('#department-filter'),departmentList=$('#department-list'),pages=$('#department-pages'),status=$('#department-create-status');
  let currentPage=1,guangzhouCityId=null,busy=false;
  const displayTime=value=>{if(!value)return '尚无记录';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'时间未知':parsed.toLocaleString()};
  const query=()=>{const params=new URLSearchParams(new FormData(filterForm));for(const[key,value]of [...params])if(!value)params.delete(key);params.set('page',String(currentPage));params.set('page_size','20');return params};
  const citySuffix=()=>filterForm.elements.city_id.value?`?city_id=${encodeURIComponent(filterForm.elements.city_id.value)}`:'';
  const renderSummary=item=>{$('#department-discovered-count').textContent=item.discovered_count;$('#department-coordinate-count').textContent=item.with_coordinates_count;$('#department-district-count').textContent=item.districts.length;$('#department-latest-seen').textContent=displayTime(item.latest_seen_at);$('#department-districts').innerHTML=item.districts.length?item.districts.map(district=>`<span class="badge">${esc(district.district)} · ${esc(district.count)}</span>`).join(''):'<span class="cache-muted">尚无行政区数据</span>'};
  const renderDepartment=item=>{const coordinate=item.latitude===null||item.longitude===null?'坐标待补充':`${esc(item.latitude)}, ${esc(item.longitude)}`;const hours=item.is_open_24h===true?'24 小时':esc(item.business_hours||'未返回营业时间');const selfService=item.self_service_pickup===true&&item.self_service_return===true?'支持自助取还':item.self_service_pickup===true?'支持自助取车':item.self_service_return===true?'支持自助还车':'未返回自助信息';return `<article class="department-item"><div><span class="badge">${esc(item.active_state_label)}</span><span class="badge">${esc(item.discovery_source_label)}</span><h3>${esc(item.name)}</h3><p>${esc(item.address||'未返回地址')}</p><small>${esc(item.district||'行政区未知')} · ${coordinate}</small></div><dl><div><dt>营业时间</dt><dd>${hours}</dd></div><div><dt>自助服务</dt><dd>${esc(selfService)}</dd></div><div><dt>首次记录</dt><dd>${esc(displayTime(item.first_seen_at))}</dd></div><div><dt>最近记录</dt><dd>${esc(displayTime(item.last_seen_at))}</dd></div></dl></article>`};
  const renderDepartments=data=>{$('#department-list-total').textContent=`共 ${data.pagination.total} 条`;departmentList.innerHTML=data.items.length?data.items.map(renderDepartment).join(''):'<div class="empty-state"><b>没有匹配的网点</b><p>调整筛选条件或重新同步广州网点目录。</p></div>';currentPage=data.pagination.page;pages.innerHTML=data.pagination.pages>1?`<button class="text-button" type="button" data-page="${esc(currentPage-1)}" ${currentPage<=1?'disabled':''}>上一页</button><span>第 ${esc(currentPage)} / ${esc(data.pagination.pages)} 页，共 ${esc(data.pagination.total)} 条</span><button class="text-button" type="button" data-page="${esc(currentPage+1)}" ${currentPage>=data.pagination.pages?'disabled':''}>下一页</button>`:''};
  const refresh=async()=>{const[summary,departments]=await Promise.all([api('/api/departments/summary'+citySuffix()),api('/api/departments?'+query())]);renderSummary(summary);renderDepartments(departments)};
  const loadCities=async()=>{const data=await api('/api/cities');filterForm.elements.city_id.innerHTML='<option value="">所有已记录城市</option>'+data.items.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('');const guangzhou=data.items.find(item=>item.zuche_city_id==='14'||item.name==='广州');guangzhouCityId=guangzhou?.id??null;if(guangzhou){filterForm.elements.city_id.value=String(guangzhou.id)}};
  const setBusy=value=>{busy=value;$('#department-city-sync').disabled=value;$('#department-directory-sync').disabled=value};
  $('#department-city-sync').onclick=async()=>{if(busy)return;setBusy(true);status.textContent='正在同步城市…';try{const data=await api('/api/zuche/cities/sync',json('POST',{}));toast(`已同步 ${data.city_count} 个开放城市`);await loadCities();currentPage=1;await refresh();status.textContent='城市已同步'}catch(error){status.textContent='城市同步失败';toast(error.message)}finally{setBusy(false)}};
  $('#department-directory-sync').onclick=async()=>{if(busy)return;if(!guangzhouCityId){toast('请先同步神州开放城市');return}setBusy(true);status.textContent='正在同步网点…';try{const data=await api('/api/zuche/departments/sync',json('POST',{city_id:Number(guangzhouCityId)}));toast(`广州目录返回 ${data.department_count} 个网点，新增 ${data.new_count} 个`);status.textContent=`目录 ${data.department_count} 个网点`;currentPage=1;filterForm.elements.city_id.value=String(guangzhouCityId);await refresh()}catch(error){status.textContent='目录同步失败';toast(error.message)}finally{setBusy(false)}};
  filterForm.onsubmit=event=>{event.preventDefault();currentPage=1;refresh().catch(error=>toast(error.message))};pages.onclick=event=>{const button=event.target.closest('[data-page]');if(button&&!button.disabled){currentPage=Number(button.dataset.page);refresh().catch(error=>toast(error.message))}};
  loadCities().then(refresh).then(()=>{status.textContent='目录已就绪'}).catch(error=>{status.textContent='读取失败';toast(error.message)});
}
const page=document.body.dataset.page;({discovery:initDiscovery,data:initDataTable,history:initHistory,admin:initAdmin,model:initModel,settings:initSettings,'settings-map-cache':initMapCache,'settings-shenzhou':initShenzhouCatalog,'settings-departments':initDepartmentDirectory,'settings-api':initApiCatalog,'settings-zuche-apis':initZucheUpstream}[page]||(()=>{}))();
