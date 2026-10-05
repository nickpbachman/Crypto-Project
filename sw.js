self.addEventListener("push", event => {
  let data={}; try { data=event.data?event.data.json():{} } catch(e) {}
  const title=data.title || "What's Happening?";
  const opts={body:data.body||"Major crypto anomaly detected.",tag:data.tag||"crypto-anomaly",data:data.data||{},renotify:true,icon:"/icon-192.png",badge:"/icon-192.png"};
  event.waitUntil(self.registration.showNotification(title,opts));
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const id=event.notification.data?.coin?.id;
  const url=id ? `/?asset=${encodeURIComponent(id)}` : "/";
  event.waitUntil(clients.matchAll({type:"window",includeUncontrolled:true}).then(list=>{
    for(const c of list){ if("focus" in c){ c.focus(); if("navigate" in c) c.navigate(url); return; }}
    return clients.openWindow(url);
  }));
});
