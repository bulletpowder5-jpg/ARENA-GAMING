(() => {
  "use strict";
  const modes = ["neonrunner","trafficdodge","spacedefender","colorswitch","mazerunner","penaltykings","wordquest","speedtap","neondodge","bubblepop"];
  const oldShowGame = window.showGame;
  const oldHideGames = window.hideGames;
  let mode="", timer=null, score=0, time=30, tick=0, px=280, py=300, objects=[], bullets=[], word="", guess="", guesses=[];
  const colors=["#65e4ff","#b58cff","#ff7bd5","#56e6bd"];
  function stop(){if(timer)clearInterval(timer);timer=null;mode="";}
  function start(id){
    stop(); mode=id; score=0; time=id==="speedtap"?15:30; tick=0; px=280; py=300; objects=[]; bullets=[]; guess=""; guesses=[];
    if(id==="wordquest") word=["LASER","GHOST","PIXEL","ROBOT","SPACE","NINJA","QUEST"][Math.floor(Math.random()*7)];
    if(id==="mazerunner"){px=1;py=1;}
    if(id==="speedtap") moveTarget();
    for(let i=0;i<7;i++) objects.push({x:20+Math.random()*550,y:30+Math.random()*250,r:10+Math.random()*8,w:24+Math.random()*18,h:18+Math.random()*15});
    draw();
    timer=setInterval(step,100);
    status("Score: 0 · Time: "+time+"s");
  }
  function status(s){const el=document.getElementById(mode+"-status");if(el)el.textContent=s;}
  function moveTarget(){px=40+Math.random()*520;py=45+Math.random()*260;}
  function step(){
    tick++; if(mode!=="wordquest")time-=0.1;
    if(["neonrunner","trafficdodge","neondodge"].includes(mode)&&tick%7===0)objects.push({x:20+Math.random()*550,y:-25,r:12,w:24+Math.random()*22,h:20+Math.random()*20});
    if(mode==="bubblepop"&&tick%6===0)objects.push({x:20+Math.random()*560,y:365,r:10+Math.random()*13});
    if(mode==="spacedefender"&&tick%8===0)objects.push({x:20+Math.random()*560,y:-15,r:10});
    objects.forEach(o=>{if(mode==="neonrunner")o.x-=5;else if(mode==="bubblepop")o.y-=2;else o.y+=3;});
    bullets.forEach(b=>b.y-=8);
    if(mode==="spacedefender"){
      objects=objects.filter(o=>{const hit=bullets.some(b=>Math.hypot(b.x-o.x,b.y-o.y)<o.r+5);if(hit)score++;return !hit&&o.y<370;});
      bullets=bullets.filter(b=>b.y>0);
    }
    if(["trafficdodge","neondodge","neonrunner"].includes(mode)){
      const hit=objects.some(o=>px<o.x+(o.w||o.r*2)&&px+28>o.x&&py<o.y+(o.h||o.r*2)&&py+28>o.y);
      if(hit)time=0;
      objects=objects.filter(o=>o.y<380&&o.x>-50);
    }
    if(mode==="penaltykings") px=100+(tick*9)%400;
    if(mode==="colorswitch"&&tick%8===0) colors.sort(()=>Math.random()-.5);
    draw();
    if(mode!=="wordquest")status("Score: "+score+" · Time: "+Math.max(0,Math.ceil(time))+"s");
    if(time<=0){clearInterval(timer);timer=null;status("Round over! Final score: "+score+". Restart to play again.");}
  }
  function draw(){
    const c=document.getElementById(mode+"-canvas"); if(!c)return;
    const x=c.getContext("2d");x.clearRect(0,0,600,360);x.fillStyle="#080f1b";x.fillRect(0,0,600,360);
    x.strokeStyle="#1b2a42";for(let i=0;i<600;i+=30){x.beginPath();x.moveTo(i,0);x.lineTo(i,360);x.stroke();}
    if(mode==="mazerunner"){
      for(let y=0;y<12;y++)for(let z=0;z<20;z++)if((z*7+y*11)%5===0&&!(z<3&&y<3)&&!(z>16&&y>9)){x.fillStyle="#34445f";x.fillRect(z*30,y*30,30,30);}
      x.fillStyle="#56e6bd";x.fillRect(550,320,25,25);x.fillStyle="#65e4ff";x.fillRect(px*30+4,py*30+4,22,22);return;
    }
    if(mode==="penaltykings"){x.strokeStyle="#eef4ff";x.strokeRect(170,35,260,110);x.fillStyle="#56e6bd";x.fillRect(px-40,70,80,18);x.fillStyle="#65e4ff";x.beginPath();x.arc(300,300,10,0,7);x.fill();x.fillStyle="#fff";x.fillText("Click inside the goal to score",190,190);return;}
    if(mode==="wordquest"){x.fillStyle="#eef4ff";x.font="bold 22px sans-serif";x.fillText("Guess the five-letter word",150,50);guesses.forEach((g,i)=>x.fillText(g,185,100+i*34));x.fillStyle="#65e4ff";x.fillText(guess.padEnd(5,"_"),250,330);return;}
    if(mode==="colorswitch"){x.fillStyle=colors[tick%colors.length];x.fillRect(205,75,190,190);x.fillStyle="#fff";x.font="bold 22px sans-serif";x.fillText("TAP THE COLOR",205,300);}
    else if(mode==="speedtap"){x.fillStyle="#b58cff";x.beginPath();x.arc(px,py,28,0,7);x.fill();x.fillStyle="#fff";x.fillText("TAP",px-12,py+5);}
    else if(mode==="spacedefender"){x.fillStyle="#65e4ff";x.beginPath();x.moveTo(px,py-18);x.lineTo(px-16,py+15);x.lineTo(px+16,py+15);x.fill();bullets.forEach(b=>{x.fillStyle="#fff";x.fillRect(b.x,b.y,4,10);});}
    else if(mode==="bubblepop"){x.fillStyle="#fff";x.fillText("Tap bubbles to pop them",20,25);}
    else {x.fillStyle="#65e4ff";x.fillRect(px,py,28,28);}
    objects.forEach(o=>{x.fillStyle=mode==="bubblepop"?"#65e4ff":"#ff7bd5";x.beginPath();x.arc(o.x,o.y,o.r||10,0,7);x.fill();});
  }
  function click(e){
    if(!mode)return;const c=e.currentTarget,r=c.getBoundingClientRect(),x=(e.clientX-r.left)*c.width/r.width,y=(e.clientY-r.top)*c.height/r.height;
    if(mode==="bubblepop"){const i=objects.findIndex(o=>Math.hypot(o.x-x,o.y-y)<o.r+8);if(i>=0){objects.splice(i,1);score++;}}
    else if(mode==="spacedefender")bullets.push({x:px,y:py-15});
    else if(mode==="speedtap"){if(Math.hypot(px-x,py-y)<38){score++;moveTarget();}}
    else if(mode==="colorswitch")score++;
    else if(mode==="penaltykings"&&x>170&&x<430&&y>35&&y<145)score+=10;
    else if(mode==="wordquest"){guess+=String.fromCharCode(65+Math.floor(Math.random()*26));if(guess.length===5){guesses.push(guess);guess="";}}
    else score++;
    draw();status("Score: "+score+" · Time: "+Math.ceil(time)+"s");
  }
  document.addEventListener("click",e=>{if(e.target&&e.target.tagName==="CANVAS"&&modes.some(id=>e.target.id===id+"-canvas"))click(e);});
  document.addEventListener("keydown",e=>{
    if(!modes.includes(mode))return;const k=e.key.toLowerCase();
    if(mode==="wordquest"){if(/^[a-z]$/.test(k)&&guess.length<5)guess+=k.toUpperCase();else if(k==="backspace")guess=guess.slice(0,-1);else if(k==="enter"&&guess.length===5){const g=guess;guesses.push(g+(g===word?" ✓":""));guess="";if(g===word){clearInterval(timer);timer=null;status("Correct! You solved the word.");}else if(guesses.length>=6){clearInterval(timer);timer=null;status("Out of guesses. The word was "+word+".");}}draw();return;}
    if(k==="arrowleft"||k==="a")px=Math.max(0,px-25);if(k==="arrowright"||k==="d")px=Math.min(570,px+25);if(k==="arrowup"||k==="w")py=Math.max(0,py-25);if(k==="arrowdown"||k==="s")py=Math.min(330,py+25);
    if(["arrowleft","arrowright","arrowup","arrowdown"].includes(k))e.preventDefault();draw();
  });
  window.startArcadeMini=start;
  window.showGame=function(name){if(!modes.includes(name)){oldShowGame(name);return;}panels.forEach(id=>{const el=document.getElementById("game-"+id);if(el)el.hidden=id!==name;});start(name);document.getElementById("game-"+name).scrollIntoView({behavior:"smooth",block:"start"});};
  window.hideGames=function(){stop();oldHideGames();};
})();