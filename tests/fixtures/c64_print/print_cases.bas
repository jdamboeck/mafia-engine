10 open 1,8,1,"out,s,w"
20 read n$:if n$="end" goto 200
30 v=val(n$):print#1,n$"|"v"|";str$(v);"|"mid$(str$(v),2)"|":goto 20
100 data 5,-500,0,2.5,-0.9,3000,22,-3.5,0.5,123456789
120 data end
200 close 1
