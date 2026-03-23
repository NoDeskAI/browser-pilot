(function(){
  'use strict';
  function mn(fn,name){fn.toString=function(){return 'function '+name+'() { [native code] }'};return fn;}

  try{Object.defineProperty(Object.getPrototypeOf(navigator),'webdriver',{get:mn(function(){return undefined},'get webdriver'),configurable:true})}catch(e){}

  try{
    Object.getOwnPropertyNames(window).forEach(function(p){if(/^cdc_/.test(p))try{delete window[p]}catch(e){}});
    Object.getOwnPropertyNames(document).forEach(function(p){if(/^\$cdc_/.test(p))try{Object.defineProperty(document,p,{get:function(){return undefined},configurable:true})}catch(e){}});
  }catch(e){}

  try{
    if(!window.chrome)window.chrome={};
    if(!window.chrome.runtime){
      window.chrome.runtime={
        connect:mn(function(){return{onDisconnect:{addListener:function(){}},onMessage:{addListener:function(){}},postMessage:function(){}};},'connect'),
        sendMessage:mn(function(a,b,c){if(typeof c==='function')c();},'sendMessage'),
      };
    }
    if(!window.chrome.csi)window.chrome.csi=mn(function(){return{}},'csi');
    if(!window.chrome.loadTimes)window.chrome.loadTimes=mn(function(){return{}},'loadTimes');
  }catch(e){}

  try{Object.defineProperty(navigator,'plugins',{get:mn(function(){
    return{0:{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',length:1},1:{name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',length:1},2:{name:'Native Client',filename:'internal-nacl-plugin',description:'',length:2},length:3,item:function(i){return this[i]||null},namedItem:function(n){for(var i=0;i<3;i++)if(this[i]&&this[i].name===n)return this[i];return null},refresh:function(){}};
  },'get plugins'),configurable:true})}catch(e){}

  try{Object.defineProperty(Object.getPrototypeOf(navigator),'languages',{get:mn(function(){return['zh-CN','zh','en-US','en']},'get languages'),configurable:true})}catch(e){}
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'hardwareConcurrency',{get:mn(function(){return 8},'get hardwareConcurrency'),configurable:true})}catch(e){}
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'deviceMemory',{get:mn(function(){return 8},'get deviceMemory'),configurable:true})}catch(e){}

  try{
    ['WebGLRenderingContext','WebGL2RenderingContext'].forEach(function(c){
      if(!window[c])return;var orig=window[c].prototype.getParameter;
      window[c].prototype.getParameter=mn(function(p){
        if(p===0x9245)return'Intel Inc.';if(p===0x9246)return'Intel Iris OpenGL Engine';return orig.call(this,p);
      },'getParameter');
    });
  }catch(e){}

  try{
    var oq=navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query=mn(function(d){
      if(d.name==='notifications')return Promise.resolve({state:'prompt',onchange:null});return oq(d);
    },'query');
  }catch(e){}

  try{
    var sv={width:1366,height:768,availWidth:1366,availHeight:728,colorDepth:24,pixelDepth:24};
    Object.keys(sv).forEach(function(k){Object.defineProperty(screen,k,{get:function(){return sv[k]},configurable:true})});
  }catch(e){}

  try{
    Object.defineProperty(window,'outerWidth',{get:function(){return window.innerWidth},configurable:true});
    Object.defineProperty(window,'outerHeight',{get:function(){return window.innerHeight+85},configurable:true});
  }catch(e){}

  try{
    var origTDU=HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL=mn(function(){
      try{var c=this.getContext('2d');if(c&&this.width>0&&this.height>0){var d=c.getImageData(0,0,1,1);d.data[3]=d.data[3]^1;c.putImageData(d,0,0)}}catch(e){}
      return origTDU.apply(this,arguments);
    },'toDataURL');
  }catch(e){}
})()
