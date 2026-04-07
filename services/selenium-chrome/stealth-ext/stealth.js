(function(){
  'use strict';
  var SEED=Date.now()^(Math.random()*4294967296>>>0);
  function xmur3(h){return function(){h=Math.imul(h^(h>>>16),2246822507);h=Math.imul(h^(h>>>13),3266489909);return((h^=(h>>>16))>>>0)/4294967296};}
  var rng=xmur3(SEED);
  function mn(fn,name){Object.defineProperty(fn,'toString',{value:function(){return'function '+name+'() { [native code] }'},enumerable:false});Object.defineProperty(fn,'name',{value:name,configurable:true});return fn;}

  // ===== 1. WebDriver property (must truly remove, not just set undefined) =====
  try{
    var navProto=Object.getPrototypeOf(navigator);
    if('webdriver' in navProto){delete navProto.webdriver}
    Object.defineProperty(navProto,'webdriver',{get:mn(function(){return false},'get webdriver'),configurable:true,enumerable:true});
  }catch(e){}

  // ===== 2. cdc_ / $cdc_ ChromeDriver artifacts =====
  try{
    var _cleanCdc=function(){
      Object.getOwnPropertyNames(window).forEach(function(p){if(/^cdc_/.test(p))try{delete window[p]}catch(e){}});
      Object.getOwnPropertyNames(document).forEach(function(p){if(/^\$cdc_/.test(p)||/^cdc_/.test(p))try{Object.defineProperty(document,p,{get:function(){return undefined},configurable:true})}catch(e){}});
    };
    _cleanCdc();
    var _obs=new MutationObserver(function(){_cleanCdc()});
    _obs.observe(document.documentElement||document,{childList:true,subtree:true});
  }catch(e){}

  // ===== 3. chrome object =====
  try{
    if(!window.chrome)window.chrome={};
    if(!window.chrome.runtime){
      window.chrome.runtime={
        connect:mn(function(){return{onDisconnect:{addListener:function(){}},onMessage:{addListener:function(){}},postMessage:function(){}};},'connect'),
        sendMessage:mn(function(a,b,c){if(typeof c==='function')c();},'sendMessage'),
        id:undefined,
      };
    }
    if(!window.chrome.csi)window.chrome.csi=mn(function(){return{onloadT:(new Date).getTime()-300,startE:(new Date).getTime()-600,pageT:3.14,tran:15}},'csi');
    if(!window.chrome.loadTimes)window.chrome.loadTimes=mn(function(){return{requestTime:Date.now()/1000-0.3,startLoadTime:Date.now()/1000-0.3,commitLoadTime:Date.now()/1000-0.2,finishDocumentLoadTime:Date.now()/1000-0.1,finishLoadTime:Date.now()/1000,firstPaintTime:Date.now()/1000-0.05,firstPaintAfterLoadTime:0,navigationType:'Other',wasFetchedViaSpdy:true,wasNpnNegotiated:true,npnNegotiatedProtocol:'h2',wasAlternateProtocolAvailable:false,connectionInfo:'h2'}},'loadTimes');
    if(!window.chrome.app)window.chrome.app={isInstalled:false,InstallState:{INSTALLED:'installed',NOT_INSTALLED:'not_installed',DISABLED:'disabled'},RunningState:{RUNNING:'running',CANNOT_RUN:'cannot_run',READY_TO_RUN:'ready_to_run'},getDetails:mn(function(){return null},'getDetails'),getIsInstalled:mn(function(){return false},'getIsInstalled')};
  }catch(e){}

  // ===== 4. Plugins & MimeTypes =====
  try{
    var realPlugins=navigator.plugins;
    if(!realPlugins||realPlugins.length===0){
      var fakePlugins={length:3};
      fakePlugins[0]={name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',description:'Portable Document Format',length:1,0:{type:'application/pdf',suffixes:'pdf',description:'Portable Document Format'}};
      fakePlugins[1]={name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',description:'',length:1,0:{type:'application/x-google-chrome-pdf',suffixes:'pdf',description:'Portable Document Format'}};
      fakePlugins[2]={name:'Native Client',filename:'internal-nacl-plugin',description:'',length:2,0:{type:'application/x-nacl',suffixes:'',description:'Native Client Executable'},1:{type:'application/x-pnacl',suffixes:'',description:'Portable Native Client Executable'}};
      fakePlugins.item=mn(function(i){return this[i]||null},'item');
      fakePlugins.namedItem=mn(function(n){for(var i=0;i<this.length;i++)if(this[i]&&this[i].name===n)return this[i];return null},'namedItem');
      fakePlugins.refresh=mn(function(){},'refresh');
      Object.setPrototypeOf(fakePlugins,PluginArray.prototype);
      Object.defineProperty(Object.getPrototypeOf(navigator),'plugins',{get:mn(function(){return fakePlugins},'get plugins'),configurable:true});
    }
  }catch(e){}

  try{
    var realMimes=navigator.mimeTypes;
    if(!realMimes||realMimes.length===0){
      var fakeMimes={length:2};
      fakeMimes[0]={type:'application/pdf',suffixes:'pdf',description:'Portable Document Format',enabledPlugin:{name:'Chrome PDF Plugin'}};
      fakeMimes[1]={type:'application/x-google-chrome-pdf',suffixes:'pdf',description:'Portable Document Format',enabledPlugin:{name:'Chrome PDF Viewer'}};
      fakeMimes.item=mn(function(i){return this[i]||null},'item');
      fakeMimes.namedItem=mn(function(n){for(var i=0;i<this.length;i++)if(this[i]&&this[i].type===n)return this[i];return null},'namedItem');
      Object.setPrototypeOf(fakeMimes,MimeTypeArray.prototype);
      Object.defineProperty(Object.getPrototypeOf(navigator),'mimeTypes',{get:mn(function(){return fakeMimes},'get mimeTypes'),configurable:true});
    }
  }catch(e){}

  // ===== 5. Navigator properties =====
  var navProps={
    languages:['zh-CN','zh','en-US','en'],
    language:'zh-CN',
    hardwareConcurrency:8,
    deviceMemory:8,
    maxTouchPoints:0,
    vendor:'Google Inc.',
    vendorSub:'',
    productSub:'20030107',
    platform:'Linux x86_64',
    appVersion:'5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
  };
  Object.keys(navProps).forEach(function(k){
    try{Object.defineProperty(Object.getPrototypeOf(navigator),k,{get:mn(function(){return navProps[k]},'get '+k),configurable:true})}catch(e){}
  });
  try{Object.defineProperty(Object.getPrototypeOf(navigator),'connection',{get:mn(function(){return{effectiveType:'4g',rtt:50,downlink:10,saveData:false,onchange:null,addEventListener:function(){},removeEventListener:function(){}}},'get connection'),configurable:true})}catch(e){}

  // ===== 6. Screen =====
  try{
    var sv={width:1920,height:1080,availWidth:1920,availHeight:1040,colorDepth:24,pixelDepth:24};
    Object.keys(sv).forEach(function(k){Object.defineProperty(screen,k,{get:function(){return sv[k]},configurable:true})});
    Object.defineProperty(screen,'orientation',{get:function(){return{angle:0,type:'landscape-primary',onchange:null}},configurable:true});
  }catch(e){}
  try{
    Object.defineProperty(window,'outerWidth',{get:function(){return window.innerWidth},configurable:true});
    Object.defineProperty(window,'outerHeight',{get:function(){return window.innerHeight+85},configurable:true});
    Object.defineProperty(window,'devicePixelRatio',{get:function(){return 1},configurable:true});
    Object.defineProperty(window,'screenX',{get:function(){return 0},configurable:true});
    Object.defineProperty(window,'screenY',{get:function(){return 0},configurable:true});
  }catch(e){}

  // ===== 7. Canvas fingerprint (toDataURL + toBlob + getImageData) =====
  try{
    var _noiseSeed=SEED;
    function _noiseCanvas(canvas){
      try{
        var ctx=canvas.getContext('2d');
        if(!ctx||canvas.width<2||canvas.height<2)return;
        var w=Math.min(canvas.width,64),h=Math.min(canvas.height,64);
        var img=ctx.getImageData(0,0,w,h);
        var d=img.data;
        for(var i=0;i<d.length;i+=4){
          var s=_noiseSeed^(i*2654435761);
          s=((s>>>16)^s)*0x45d9f3b;
          d[i]=(d[i]+(((s>>>0)%3)-1))&255;
          d[i+1]=(d[i+1]+(((s>>>8)%3)-1))&255;
          d[i+2]=(d[i+2]+(((s>>>16)%3)-1))&255;
        }
        ctx.putImageData(img,0,0);
      }catch(e){}
    }
    var origTDU=HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL=mn(function(){_noiseCanvas(this);return origTDU.apply(this,arguments)},'toDataURL');
    var origTB=HTMLCanvasElement.prototype.toBlob;
    if(origTB){HTMLCanvasElement.prototype.toBlob=mn(function(){_noiseCanvas(this);return origTB.apply(this,arguments)},'toBlob');}
    var origGID=CanvasRenderingContext2D.prototype.getImageData;
    var _gidCount=0;
    CanvasRenderingContext2D.prototype.getImageData=mn(function(sx,sy,sw,sh){
      var img=origGID.call(this,sx,sy,sw,sh);
      if(++_gidCount%3===0){
        var d=img.data;
        for(var i=0;i<Math.min(d.length,256);i+=4){d[i]=(d[i]+(((_noiseSeed^i)%3)-1))&255}
      }
      return img;
    },'getImageData');
  }catch(e){}

  // ===== 8. WebGL fingerprint =====
  try{
    var glVendor='Intel Inc.';
    var glRenderer='Intel Iris OpenGL Engine';
    ['WebGLRenderingContext','WebGL2RenderingContext'].forEach(function(c){
      if(!window[c])return;
      var proto=window[c].prototype;
      var origGP=proto.getParameter;
      proto.getParameter=mn(function(p){
        if(p===0x9245)return glVendor;
        if(p===0x9246)return glRenderer;
        if(p===0x1F00)return glVendor;
        if(p===0x1F01)return glRenderer;
        if(p===0x1F02)return'OpenGL ES 3.0 (ANGLE)';
        if(p===0x8B8C)return'WebGL GLSL ES 3.00 (ANGLE)';
        return origGP.call(this,p);
      },'getParameter');

      var origGE=proto.getExtension;
      proto.getExtension=mn(function(name){
        if(name==='WEBGL_debug_renderer_info')return{UNMASKED_VENDOR_WEBGL:0x9245,UNMASKED_RENDERER_WEBGL:0x9246};
        return origGE.call(this,name);
      },'getExtension');

      var origRP=proto.readPixels;
      proto.readPixels=mn(function(){
        origRP.apply(this,arguments);
        var pixels=arguments[6];
        if(pixels&&pixels.length){for(var i=0;i<Math.min(pixels.length,128);i+=4){pixels[i]=(pixels[i]+(((SEED^i)%3)-1))&255}}
      },'readPixels');

      var origGSE=proto.getSupportedExtensions;
      proto.getSupportedExtensions=mn(function(){
        var exts=origGSE.call(this)||[];
        return exts.filter(function(e){return e!=='WEBGL_debug_shader_precision_format'});
      },'getSupportedExtensions');
    });
  }catch(e){}

  // ===== 9. AudioContext fingerprint =====
  try{
    var OAC=window.OfflineAudioContext||window.webkitOfflineAudioContext;
    if(OAC){
      var origGCD=AudioBuffer.prototype.getChannelData;
      AudioBuffer.prototype.getChannelData=mn(function(ch){
        var buf=origGCD.call(this,ch);
        if(buf&&buf.length>100){
          for(var i=0;i<Math.min(buf.length,512);i++){buf[i]+=(rng()-0.5)*1e-7}
        }
        return buf;
      },'getChannelData');

      var origCPN=AudioBuffer.prototype.copyFromChannel;
      if(origCPN){
        AudioBuffer.prototype.copyFromChannel=mn(function(dest,ch,off){
          origCPN.call(this,dest,ch,off);
          if(dest&&dest.length>100){for(var i=0;i<Math.min(dest.length,512);i++){dest[i]+=(rng()-0.5)*1e-7}}
        },'copyFromChannel');
      }
    }
  }catch(e){}

  // ===== 10. WebRTC leak protection =====
  try{
    if(window.RTCPeerConnection){
      var OrigRTC=window.RTCPeerConnection;
      window.RTCPeerConnection=mn(function(cfg,cons){
        if(cfg&&cfg.iceServers){cfg.iceServers=cfg.iceServers.filter(function(s){return!(/stun:|turn:/).test(JSON.stringify(s))})}
        var pc=new OrigRTC(cfg,cons);
        var origCIC=pc.createDataChannel;
        return pc;
      },'RTCPeerConnection');
      window.RTCPeerConnection.prototype=OrigRTC.prototype;
      if(window.webkitRTCPeerConnection)window.webkitRTCPeerConnection=window.RTCPeerConnection;
    }
  }catch(e){}

  // ===== 11. Battery API =====
  try{
    if(navigator.getBattery){
      navigator.getBattery=mn(function(){return Promise.resolve({charging:true,chargingTime:0,dischargingTime:Infinity,level:1,addEventListener:function(){},removeEventListener:function(){},onchargingchange:null,onchargingtimechange:null,ondischargingtimechange:null,onlevelchange:null})},'getBattery');
    }
  }catch(e){}

  // ===== 12. MediaDevices =====
  try{
    if(navigator.mediaDevices&&navigator.mediaDevices.enumerateDevices){
      var origED=navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
      navigator.mediaDevices.enumerateDevices=mn(function(){
        return origED().then(function(devices){
          if(devices.length===0){
            return[
              {deviceId:'default',kind:'audioinput',label:'',groupId:'ag1'},
              {deviceId:'comm',kind:'audiooutput',label:'',groupId:'ag2'},
              {deviceId:'vid1',kind:'videoinput',label:'',groupId:'vg1'},
            ];
          }
          return devices;
        });
      },'enumerateDevices');
    }
  }catch(e){}

  // ===== 13. SpeechSynthesis voices =====
  try{
    if(window.speechSynthesis){
      var origGV=window.speechSynthesis.getVoices;
      window.speechSynthesis.getVoices=mn(function(){
        var v=origGV.call(window.speechSynthesis);
        if(!v||v.length===0){
          return[{voiceURI:'Google 普通话（中国大陆）',name:'Google 普通话（中国大陆）',lang:'zh-CN',localService:false,default:true},{voiceURI:'Google US English',name:'Google US English',lang:'en-US',localService:false,default:false}];
        }
        return v;
      },'getVoices');
    }
  }catch(e){}

  // ===== 14. ClientRect noise =====
  try{
    var origGBCR=Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect=mn(function(){
      var r=origGBCR.call(this);
      var n=(SEED^this.tagName.length)%7*0.00001;
      return new DOMRect(r.x+n,r.y+n,r.width+n,r.height+n);
    },'getBoundingClientRect');
    var origGCR=Element.prototype.getClientRects;
    Element.prototype.getClientRects=mn(function(){
      var rects=origGCR.call(this);
      return rects;
    },'getClientRects');
  }catch(e){}

  // ===== 15. Permissions =====
  try{
    var oq=navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query=mn(function(d){
      if(d.name==='notifications')return Promise.resolve({state:Notification.permission||'default',onchange:null,addEventListener:function(){},removeEventListener:function(){}});
      return oq(d);
    },'query');
  }catch(e){}

  // ===== 16. Notification =====
  try{
    if(!window.Notification)window.Notification={permission:'default',requestPermission:mn(function(){return Promise.resolve('default')},'requestPermission'),maxActions:2};
  }catch(e){}

  // ===== 17. iframe contentWindow chrome =====
  try{
    var origCW=Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'contentWindow');
    if(origCW&&origCW.get){
      Object.defineProperty(HTMLIFrameElement.prototype,'contentWindow',{get:mn(function(){var w=origCW.get.call(this);if(w){try{w.chrome=window.chrome}catch(e){}}return w},'get contentWindow')});
    }
  }catch(e){}

  // ===== 18. Block history.back / window.close =====
  try{
    Object.defineProperty(History.prototype,'back',{value:mn(function(){},'back'),writable:false,configurable:false});
    var origGo=History.prototype.go;
    Object.defineProperty(History.prototype,'go',{value:mn(function(n){if(n<0)return;return origGo.call(this,n);},'go'),writable:false,configurable:false});
  }catch(e){}
  try{window.close=mn(function(){},'close')}catch(e){}

  // ===== 19. Error stack trace cleanup =====
  try{
    var origErr=Error;
    var errProxy=new Proxy(origErr,{construct:function(t,a){var e=new t(...a);if(e.stack)e.stack=e.stack.replace(/\n\s+at Object\.apply.*$/gm,'').replace(/\n\s+at (?:Object\.)?(?:callFunction|Runtime\.evaluate).*$/gm,'');return e}});
    window.Error=errProxy;
  }catch(e){}

  // ===== 20. Date / Intl consistency =====
  try{
    var origDTF=Intl.DateTimeFormat;
    var dtfProxy=new Proxy(origDTF,{construct:function(t,a){
      if(!a[1]||!a[1].timeZone)a[1]=Object.assign({},a[1]||{},{timeZone:'Asia/Shanghai'});
      return new t(...a);
    }});
    Intl.DateTimeFormat=dtfProxy;
    Object.defineProperty(Intl.DateTimeFormat,'prototype',{value:origDTF.prototype});
  }catch(e){}

  // ===== 21. Performance timing protection =====
  try{
    var origNow=Performance.prototype.now;
    Performance.prototype.now=mn(function(){return Math.round(origNow.call(this)*20)/20},'now');
  }catch(e){}

  // ===== 22. Clean automation markers (delete, not redefine) =====
  try{
    ['_phantom','__nightmare','callPhantom','phantom','domAutomation','domAutomationController',
     '_Selenium_IDE_Recorder','_selenium','calledSelenium','_WEBDRIVER_ELEM_CACHE',
     '__webdriver_evaluate','__selenium_evaluate','__webdriver_script_fn',
     '__webdriver_script_func','__webdriver_script_function','__driver_evaluate',
     '__driver_unwrapped','__webdriver_unwrapped','__selenium_unwrapped',
     '__fxdriver_evaluate','__fxdriver_unwrapped','$chrome_asyncScriptInfo'
    ].forEach(function(p){try{delete window[p]}catch(e){}; try{delete document[p]}catch(e){}});
  }catch(e){}

  // ===== 23. document properties =====
  try{
    Object.defineProperty(document,'hidden',{get:function(){return false},configurable:true});
    Object.defineProperty(document,'visibilityState',{get:function(){return'visible'},configurable:true});
    Object.defineProperty(document,'hasFocus',{value:mn(function(){return true},'hasFocus'),configurable:true});
  }catch(e){}

})()