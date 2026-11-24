(function(){
  var log=[];
  var labs=document.querySelectorAll("label.ant-checkbox-wrapper");
  for(var i=0;i<labs.length;i++){var tx=(labs[i].textContent||"").trim();var inp=labs[i].querySelector("input");
    if(tx==="Top Creative"&&inp&&!inp.checked){inp.click();log.push("Top Creative ON");}
    if(tx==="Only New Ads"&&inp&&inp.checked){inp.click();log.push("Only New OFF");}}
  ["Facebook Ads","TikTok"].forEach(function(name){
    var ls=document.querySelectorAll("label.ant-checkbox-wrapper, button.ant-btn");
    for(var j=0;j<ls.length;j++){if((ls[j].textContent||"").trim()===name){var inp=ls[j].querySelector("input");
      if(inp){if(!inp.checked){inp.click();log.push(name+" ON");}}else{ls[j].click();log.push(name+" ON");}return;}}});
  var rads=document.querySelectorAll("label.ant-radio-button-wrapper");
  for(var k=0;k<rads.length;k++){if((rads[k].textContent||"").trim()==="30 Days"){if((rads[k].className||"").indexOf("checked")<0){rads[k].click();log.push("30d");}break;}}
  return log.join(" | ");
})()
