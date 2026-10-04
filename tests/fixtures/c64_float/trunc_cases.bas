10 g=0:t=0:u=0:a=peek(45)+256*peek(46):x$="0123456789abcdef":dim e(11),w(24)
20 open 1,8,1,"out,s,w"
30 read l$:if l$="end" goto 100
40 g=val(l$):gosub 900:goto 30
100 for i=1 to 11:read e(i):next:for i=1 to 24:read w(i):next
105 for xi=1 to 11:x8=e(xi):g=0
110 for sn=1 to 24:x=w(sn):g=g+(x*x8):if g>100 then g=100
120 if g<0 then g=0
130 l$="s"+mid$(str$(xi),2)+"-"+mid$(str$(sn),2):gosub 900:g=t:next
140 next
200 for di=1 to 8:read l$:g=val(l$)
210 for i=1 to 60:l$="d"+mid$(str$(di),2)+"-"+mid$(str$(i),2):gosub 900:if t=g then 230
220 g=t:next
230 next di
300 for k=0 to 10000:g=k/100:t=int(g*100)/100:if t=g then print#1,"=":goto 320
310 p=a+9:gosub 800:print#1,o$
320 next
330 print#1,"end":close 1:end
800 o$="":for j=0 to 4:b=peek(p+j):o$=o$+mid$(x$,int(b/16)+1,1)+mid$(x$,(b and 15)+1,1):next:return
900 t=int(g*100)/100:p=a+2:gosub 800:gg$=o$:p=a+9:gosub 800
910 print#1,l$":"gg$":"o$":"str$(g)":"str$(t):return
1000 data 25.4,25.2,51.2,.29,.57,1.13,25.199999,33.337,99.99999999,100,0,1,2,7,50,99
1010 data 25.39,25.5,51.25,.0078125,.125,.5,11.1,25.1953125,88.8,99.9990234375,101.5
1020 data -.0078125,-.125,-2.9990234375,-3.5,-12.345,-25.4,-.29,99.99,.01,.1,.3,12.34
1030 data end
1100 data .1,.2,.3,.5,.7,1.1,1.3,1.5,1.7,1.9,2
1110 data 1,2,3,1,6,2,-2,3,1,1,2,6,-10,3,2,1,6,6,2,-2,1,3,2,1
1200 data 25.4,51.2,.29,.57,1.13,99.99,88.8,12.34
