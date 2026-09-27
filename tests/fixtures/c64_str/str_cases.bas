10 open 1,8,1,"out,s,w"
20 read n$:if n$="end" goto 200
30 v=val(n$):print#1,n$"|"str$(v)"|":goto 20
100 data 22,-0.9,0.5,-0.5,100.25,0,-0,-3.5,0.01,-0.01,0.001,0.0099,123456789,999999999,999999999.6,1e9,1234567890,-1e9
110 data 1e-10,123.456789123,0.123456789,0.1234567891,1e38,2.5e-5,25.2,12.5,3000,-500,65535
120 data end
200 print#1,"1/3|"str$(1/3)"|":print#1,"2/3|"str$(2/3)"|"
210 print#1,".1+.2-.3|"str$(.1+.2-.3)"|"
220 x8=.1:g=0:for i=1to3:g=g+(1*x8):next:for i=1to3:g=g+(-1*x8):next:print#1,"acc01|"str$(g)"|"
230 x8=.3:g=0:for i=1to7:g=g+(2*x8):next:for i=1to7:g=g+(-2*x8):next:print#1,"acc03|"str$(g)"|"
240 x8=.7:g=12.5:for i=1to3:g=g+x8*2*-(g>0):next:print#1,"acc07|"str$(g)"|"
250 x8=.1:g=0:for i=1to25:g=g+(1*x8):next:print#1,"acc25|"str$(g)"|"
260 x8=.3:g=25:g=g+x8*-1:g=g+x8*-1:print#1,"acc253|"str$(g)"|"
270 g=int(25.4*100)/100:print#1,"rnd|"str$(g)"|"
280 close 1
