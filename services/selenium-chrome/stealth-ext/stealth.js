(function(){
  'use strict';
  var _origSX=window.screenX||0,_origSY=window.screenY||0;
  var _origOH=window.outerHeight||0,_origIH=window.innerHeight||0;
  var _chromeH=(_origOH>_origIH)?(_origOH-_origIH):0;
  try{Object.defineProperty(window,'__nwb_vp_offset',{value:{x:_origSX,y:_origSY+_chromeH},writable:false,configurable:false,enumerable:false})}catch(e){}
  var SEED=__FP__.seed;
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
  var navProps=__FP__.navigator;
  Object.keys(navProps).forEach(function(k){
    try{Object.defineProperty(Object.getPrototypeOf(navigator),k,{get:mn(function(){return navProps[k]},'get '+k),configurable:true})}catch(e){}
  });
  try{var fpConn=__FP__.connection||{effectiveType:'4g',rtt:50,downlink:10,saveData:false};Object.defineProperty(Object.getPrototypeOf(navigator),'connection',{get:mn(function(){return{effectiveType:fpConn.effectiveType,rtt:fpConn.rtt,downlink:fpConn.downlink,saveData:fpConn.saveData,onchange:null,addEventListener:function(){},removeEventListener:function(){}}},'get connection'),configurable:true})}catch(e){}

  // ===== 5b. navigator.userAgentData (Client Hints API) =====
  try{
    var _hints=__FP__.clientHints||{};
    var _cv=__FP__.chromeVersion||'124.0.0.0';
    var _cmaj=_cv.split('.')[0];
    var _brands=[
      {brand:'Chromium',version:_cmaj},
      {brand:'Google Chrome',version:_cmaj},
      {brand:'Not=A?Brand',version:'99'}
    ];
    var _fullVersionList=[
      {brand:'Chromium',version:_cv},
      {brand:'Google Chrome',version:_cv},
      {brand:'Not=A?Brand',version:'99.0.0.0'}
    ];
    var _uadPlatform=_hints.platform||'Linux';
    var _uadMobile=!!_hints.mobile;
    var _heValues={
      brands:_brands,
      fullVersionList:_fullVersionList,
      platform:_uadPlatform,
      platformVersion:_hints.platformVersion||'10.0.0',
      architecture:_hints.architecture||'x86',
      bitness:_hints.bitness||'64',
      model:'',
      mobile:_uadMobile,
      wow64:!!_hints.wow64,
      uaFullVersion:_cv
    };
    var _uad={
      brands:_brands,
      mobile:_uadMobile,
      platform:_uadPlatform,
      getHighEntropyValues:mn(function(hints){
        var result={};
        for(var i=0;i<hints.length;i++){
          var h=hints[i];
          if(_heValues[h]!==undefined)result[h]=_heValues[h];
        }
        result.brands=_brands;
        result.mobile=_uadMobile;
        result.platform=_uadPlatform;
        return Promise.resolve(result);
      },'getHighEntropyValues'),
      toJSON:mn(function(){return{brands:_brands,mobile:_uadMobile,platform:_uadPlatform}},'toJSON')
    };
    if(window.NavigatorUAData){Object.setPrototypeOf(_uad,NavigatorUAData.prototype)}
    Object.defineProperty(Object.getPrototypeOf(navigator),'userAgentData',{get:mn(function(){return _uad},'get userAgentData'),configurable:true,enumerable:true});
  }catch(e){}

  // ===== 6. Screen =====
  try{
    var fpScreen=__FP__.screen;
    Object.defineProperty(screen,'colorDepth',{get:function(){return fpScreen.colorDepth},configurable:true});
    Object.defineProperty(screen,'pixelDepth',{get:function(){return fpScreen.pixelDepth},configurable:true});
    Object.defineProperty(screen,'orientation',{get:function(){return{angle:0,type:'landscape-primary',onchange:null}},configurable:true});
    if(fpScreen.width){
      Object.defineProperty(screen,'width',{get:function(){return fpScreen.width},configurable:true});
      Object.defineProperty(screen,'availWidth',{get:function(){return fpScreen.width},configurable:true});
    }
    if(fpScreen.height){
      Object.defineProperty(screen,'height',{get:function(){return fpScreen.height},configurable:true});
      Object.defineProperty(screen,'availHeight',{get:function(){return fpScreen.height-40},configurable:true});
    }
  }catch(e){}
  try{
    Object.defineProperty(window,'outerWidth',{get:function(){return window.innerWidth},configurable:true});
    Object.defineProperty(window,'outerHeight',{get:function(){return window.innerHeight+85},configurable:true});
    Object.defineProperty(window,'devicePixelRatio',{get:function(){return __FP__.devicePixelRatio},configurable:true});
    Object.defineProperty(window,'screenX',{get:function(){return 0},configurable:true});
    Object.defineProperty(window,'screenY',{get:function(){return 0},configurable:true});
  }catch(e){}

  // ===== 7. Canvas fingerprint (deterministic noise based on pixel content) =====
  try{
    var _noiseSeed=SEED;
    function _pixelHash(data,len){
      var h=_noiseSeed;
      var step=Math.max(1,Math.floor(len/256))*4;
      for(var i=0;i<len;i+=step){
        h=Math.imul(h^data[i],0x5bd1e995);
        h^=(h>>>13);
      }
      return h>>>0;
    }
    function _noiseCanvas(canvas){
      try{
        var ctx=canvas.getContext('2d');
        if(!ctx||canvas.width<2||canvas.height<2)return;
        var w=Math.min(canvas.width,64),h=Math.min(canvas.height,64);
        var img=ctx.getImageData(0,0,w,h);
        var d=img.data;
        var ph=_pixelHash(d,d.length);
        for(var i=0;i<d.length;i+=4){
          var s=ph^(i*2654435761);
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
    CanvasRenderingContext2D.prototype.getImageData=mn(function(sx,sy,sw,sh){
      var img=origGID.call(this,sx,sy,sw,sh);
      var d=img.data;
      if(d.length>=16){
        var ph=_pixelHash(d,d.length);
        for(var i=0;i<Math.min(d.length,256);i+=4){
          var s=ph^(i*2654435761);
          s=((s>>>16)^s)*0x45d9f3b;
          d[i]=(d[i]+(((s>>>0)%3)-1))&255;
        }
      }
      return img;
    },'getImageData');
  }catch(e){}

  // ===== 8. WebGL fingerprint =====
  try{
    var glVendor=__FP__.webgl.vendor;
    var glRenderer=__FP__.webgl.renderer;
    var webglP=__FP__.webgl.params;
    var glMap=null;
    if(webglP){
      glMap={
        0x0D33:function(){return webglP.maxTextureSize},
        0x84E8:function(){return webglP.maxRenderbufferSize},
        0x0D3D:function(){return new Int32Array(webglP.maxViewportDims)},
        0x8869:function(){return webglP.maxVertexAttribs},
        0x8DFC:function(){return webglP.maxVaryingVectors},
        0x8DFB:function(){return webglP.maxVertexUniformVectors},
        0x8DFD:function(){return webglP.maxFragmentUniformVectors},
        0x8872:function(){return webglP.maxTextureImageUnits},
        0x8B4D:function(){return webglP.maxCombinedTextureImageUnits},
        0x8B4C:function(){return webglP.maxVertexTextureImageUnits},
        0x846E:function(){return new Float32Array(webglP.aliasedLineWidthRange)},
        0x8460:function(){return new Float32Array(webglP.aliasedPointSizeRange)}
      };
    }
    ['WebGLRenderingContext','WebGL2RenderingContext'].forEach(function(c){
      if(!window[c])return;
      var proto=window[c].prototype;
      var origGP=proto.getParameter;
      proto.getParameter=mn(function(p){
        if(glMap&&glMap[p])return glMap[p]();
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

  // ===== 9b. AudioContext property overrides =====
  try{
    var fpAudio=__FP__.audio;
    if(fpAudio&&fpAudio.sampleRate){
      if(window.AudioContext){
        var OrigAudioCtx=window.AudioContext;
        window.AudioContext=mn(function(opts){return new OrigAudioCtx(Object.assign({},opts,{sampleRate:fpAudio.sampleRate}))},'AudioContext');
        window.AudioContext.prototype=OrigAudioCtx.prototype;
        Object.defineProperty(OrigAudioCtx.prototype,'baseLatency',{get:mn(function(){return fpAudio.baseLatency},'get baseLatency'),configurable:true});
        Object.defineProperty(OrigAudioCtx.prototype,'outputLatency',{get:mn(function(){return fpAudio.outputLatency},'get outputLatency'),configurable:true});
      }
      var OrigOAC=window.OfflineAudioContext||window.webkitOfflineAudioContext;
      if(OrigOAC){
        window.OfflineAudioContext=mn(function(channels,length,sr){return new OrigOAC(channels,length,fpAudio.sampleRate)},'OfflineAudioContext');
        window.OfflineAudioContext.prototype=OrigOAC.prototype;
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
      var _fpLang=__FP__.navigator.language||'en-US';
      var _isZh=_fpLang.indexOf('zh')===0;
      window.speechSynthesis.getVoices=mn(function(){
        var v=origGV.call(window.speechSynthesis);
        if(!v||v.length===0){
          if(_isZh){
            return[{voiceURI:'Google 普通话（中国大陆）',name:'Google 普通话（中国大陆）',lang:'zh-CN',localService:false,default:true},{voiceURI:'Google US English',name:'Google US English',lang:'en-US',localService:false,default:false}];
          }
          return[{voiceURI:'Google US English',name:'Google US English',lang:'en-US',localService:false,default:true},{voiceURI:'Google UK English Female',name:'Google UK English Female',lang:'en-GB',localService:false,default:false}];
        }
        return v;
      },'getVoices');
    }
  }catch(e){}

  // ===== 14. ClientRect noise (deterministic per element) =====
  try{
    function _elemHash(el){
      var s=SEED;
      var tag=el.tagName||'';
      for(var i=0;i<tag.length;i++){s=Math.imul(s^tag.charCodeAt(i),0x5bd1e995);s^=(s>>>13);}
      var cls=el.className||'';
      if(typeof cls==='string')for(var j=0;j<cls.length&&j<64;j++){s=Math.imul(s^cls.charCodeAt(j),0x5bd1e995);s^=(s>>>13);}
      var txt=(el.textContent||'').slice(0,32);
      for(var k=0;k<txt.length;k++){s=Math.imul(s^txt.charCodeAt(k),0x5bd1e995);s^=(s>>>13);}
      return((s>>>0)%1000)*0.0000001;
    }
    var origGBCR=Element.prototype.getBoundingClientRect;
    Element.prototype.getBoundingClientRect=mn(function(){
      var r=origGBCR.call(this);
      var n=_elemHash(this);
      return new DOMRect(r.x+n,r.y+n,r.width+n,r.height+n);
    },'getBoundingClientRect');
    var origGCR=Element.prototype.getClientRects;
    Element.prototype.getClientRects=mn(function(){
      var rects=origGCR.call(this);
      if(!rects||!rects.length)return rects;
      var n=_elemHash(this);
      var out=[];
      for(var i=0;i<rects.length;i++){var r=rects[i];out.push(new DOMRect(r.x+n,r.y+n,r.width+n,r.height+n));}
      return out;
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
    var _fpTz=__FP__.timezone||'UTC';
    var origDTF=Intl.DateTimeFormat;
    var dtfProxy=new Proxy(origDTF,{construct:function(t,a){
      if(!a[1]||!a[1].timeZone)a[1]=Object.assign({},a[1]||{},{timeZone:_fpTz});
      return new t(...a);
    }});
    Intl.DateTimeFormat=dtfProxy;
    Object.defineProperty(Intl.DateTimeFormat,'prototype',{value:origDTF.prototype});
  }catch(e){}

  // ===== 20b. Date.prototype.getTimezoneOffset =====
  try{
    var _tzOffsetMap={'UTC':0,'GMT':0,'America/New_York':-300,'America/Chicago':-360,'America/Denver':-420,'America/Los_Angeles':-480,'America/Anchorage':-540,'Pacific/Honolulu':-600,'Europe/London':0,'Europe/Berlin':60,'Europe/Moscow':180,'Asia/Tokyo':540,'Asia/Shanghai':480,'Asia/Kolkata':330,'Australia/Sydney':600,'Pacific/Auckland':720,'Asia/Hong_Kong':480,'Asia/Singapore':480,'Asia/Seoul':540,'Asia/Taipei':480};
    var _targetOffset=_tzOffsetMap[_fpTz];
    if(_targetOffset===undefined){
      try{
        var _jan=new origDTF('en-US',{timeZone:_fpTz,timeZoneName:'shortOffset'}).formatToParts(new Date(2024,0,1));
        var _offPart=_jan.find(function(p){return p.type==='timeZoneName'});
        if(_offPart){
          var _m=_offPart.value.match(/GMT([+-]?)(\d{1,2})(?::(\d{2}))?/);
          if(_m){var _sign=_m[1]==='-'?-1:1;_targetOffset=_sign*(parseInt(_m[2],10)*60+(parseInt(_m[3]||'0',10)))}
          else _targetOffset=0;
        }else _targetOffset=0;
      }catch(ex){_targetOffset=0}
    }
    var _origGTZO=Date.prototype.getTimezoneOffset;
    Date.prototype.getTimezoneOffset=mn(function(){return-_targetOffset},'getTimezoneOffset');
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

  // ===== 24. Font list spoofing =====
  try{
    var fpFonts=__FP__.fonts;
    if(fpFonts&&fpFonts.length&&document.fonts&&document.fonts.check){
      var fontSet={};
      fpFonts.forEach(function(f){fontSet[f.toLowerCase()]=true});
      var origCheck=document.fonts.check.bind(document.fonts);
      document.fonts.check=mn(function(font,text){
        var familyStr=font.replace(/^.*?\d+(\.\d+)?(px|pt|em|rem|%|vw|vh)\s*/i,'');
        var families=familyStr.split(',');
        for(var i=0;i<families.length;i++){
          var f=families[i].trim().replace(/^['"]|['"]$/g,'').toLowerCase();
          if(f&&fontSet[f])return true;
        }
        return false;
      },'check');
    }
  }catch(e){}

  // ===== 25. measureText width shift for claimed fonts =====
  try{
    var _mtFonts=__FP__.fonts;
    if(_mtFonts&&_mtFonts.length){
      var _mtSet={};
      _mtFonts.forEach(function(f){_mtSet[f.toLowerCase()]=true});
      var origMT=CanvasRenderingContext2D.prototype.measureText;
      CanvasRenderingContext2D.prototype.measureText=mn(function(text){
        var m=origMT.call(this,text);
        var fam=(this.font||'').replace(/^.*?\d+(\.\d+)?(px|pt|em|rem|%|vw|vh)\s*/i,'');
        var parts=fam.split(',');
        for(var i=0;i<parts.length;i++){
          var f=parts[i].trim().replace(/^['"]|['"]$/g,'').toLowerCase();
          if(f&&_mtSet[f]){
            var h=0;for(var j=0;j<f.length;j++)h=(h*31+f.charCodeAt(j))|0;
            var shift=((h&0x7fffffff)%100)*0.001+0.01;
            var w=m.width+shift;
            return{width:w,actualBoundingBoxLeft:m.actualBoundingBoxLeft,actualBoundingBoxRight:m.actualBoundingBoxRight+(shift),actualBoundingBoxAscent:m.actualBoundingBoxAscent,actualBoundingBoxDescent:m.actualBoundingBoxDescent,fontBoundingBoxAscent:m.fontBoundingBoxAscent,fontBoundingBoxDescent:m.fontBoundingBoxDescent,alphabeticBaseline:m.alphabeticBaseline,emHeightAscent:m.emHeightAscent,emHeightDescent:m.emHeightDescent};
          }
        }
        return m;
      },'measureText');
    }
  }catch(e){}

})()
