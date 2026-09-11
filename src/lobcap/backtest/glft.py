"""Causal GLFT quote model in ticks and units of configured order size.

Arrival intensity is an observable aggregate-trade crossing proxy, not own fills.
One observation can cross several quote distances; pooled rate is per side/second.
"""
from collections import deque
from decimal import Decimal as D,localcontext
import math

NS=1_000_000_000
DEPTHS=(1,2,4,8,16,32,64,128)
VERSION='glft-crossing-v1'


def fit_intensity(counts,seconds,min_trades,min_r2):
    points=[(float(x),math.log(n/(2*seconds))) for x,n in zip(DEPTHS,counts) if n>0]
    if counts[0]<min_trades or len(points)<3 or len({y for _,y in points})<3:
        return {'status':'insufficient_crossings','fit_points':len(points)}
    mx=sum(x for x,y in points)/len(points);my=sum(y for x,y in points)/len(points)
    slope=sum((x-mx)*(y-my) for x,y in points)/sum((x-mx)**2 for x,y in points)
    intercept=my-slope*mx
    total=sum((y-my)**2 for x,y in points)
    r2=1-sum((y-intercept-slope*x)**2 for x,y in points)/total
    if slope>=0 or r2<float(min_r2) or not -30<intercept<30:
        return {'status':'unstable_intensity_fit','r2':r2,'fit_points':len(points)}
    return {'status':'ready','A':D(str(math.exp(intercept))),'k':D(str(-slope)),
            'r2':r2,'fit_points':len(points)}


def coefficients(A,k,sigma,gamma):
    """GLFT approximation xi=gamma, Delta=1; sigma in ticks/sqrt(second)."""
    with localcontext() as ctx:
        ctx.prec=50
        if min(A,k,gamma)<=0 or sigma<0:raise ValueError('invalid GLFT coefficients')
        log=(1+gamma/k).ln()
        c1=log/gamma
        c2=(gamma/(2*A*k)*((k/gamma+1)*log).exp()).sqrt()
        skew=sigma*c2
        return c1+skew/2,skew


class Calibration:
    def __init__(self,parameters):
        self.p=parameters
        self.rows=deque(maxlen=parameters.glft_window_seconds)
        self.last=None;self.previous_mid=None;self.previous_fresh=False
        self.counts=[0]*len(DEPTHS)
        self.excluded=0;self.interval_excluded=0
        self.estimate={'status':'warmup','valid_seconds':0,'version':VERSION}

    def trade(self,now,exchange_ns,price,side,quantity,mid,fresh):
        if quantity<=0:return
        if self.last is None or not fresh or now-exchange_ns>NS:
            self.excluded+=1;self.interval_excluded+=1;return
        depth=(price-mid if side=='sell' else mid-price)/self.p.tick
        for i,threshold in enumerate(DEPTHS):
            if depth>=threshold:self.counts[i]+=1

    def on_second(self,now,mid,fresh):
        if self.last is not None:
            valid=now-self.last==NS and fresh and self.previous_fresh
            delta=(mid-self.previous_mid)/self.p.tick
            self.rows.append((tuple(self.counts),delta) if valid else None)
        self.last=now;self.previous_mid=mid;self.previous_fresh=fresh
        self.counts=[0]*len(DEPTHS)
        valid=[r for r in self.rows if r is not None]
        seconds=len(valid)
        counts=[sum(r[0][i] for r in valid) for i in range(len(DEPTHS))]
        common={'version':VERSION,'time_ns':now,'window_seconds':len(self.rows),'valid_seconds':seconds,
            'coverage':seconds/len(self.rows) if self.rows else 0,'depth_ticks':DEPTHS,'crossing_counts':counts,
            'excluded_trade_observations':self.excluded,'estimation_clock':'observable_delivery'}
        if seconds<self.p.glft_min_seconds or common['coverage']<.8:
            self.estimate={**common,'status':'warmup_or_stale'};return
        mean=sum((r[1] for r in valid),D(0))/seconds
        sigma=(sum(((r[1]-mean)**2 for r in valid),D(0))/seconds).sqrt()
        fit=fit_intensity(counts,seconds,self.p.glft_min_trades,self.p.glft_min_r2)
        self.estimate={**common,**fit,'sigma_ticks_sqrt_second':sigma}

    def snapshot(self,now):
        result=dict(self.estimate)
        if self.last is None or now-self.last>NS:result['status']='stale_estimate'
        return result
