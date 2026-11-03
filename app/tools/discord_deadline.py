"""Race-safe phase deadline, separate from explicit user cancellation."""
import threading

MODEL_DEADLINE_SECONDS=600

class ReplyDeadline:
 def __init__(self,job,timer_factory=None):
  self.job=job;self.factory=timer_factory or threading.Timer;self.lock=threading.RLock();self.timer=None;self.serial=0;self.closed=False
 def arm(self,seconds,phase):
  with self.lock:
   if self.closed:return
   self.serial+=1;serial=self.serial
   if self.timer:self.timer.cancel()
   def expired():
    with self.lock:
     if self.closed or serial!=self.serial or self.job['generation'].cancelled.is_set():return
     self.job['discord_timeout']={'phase':phase,'seconds':seconds}
     self.job['generation'].cancel()
   self.timer=self.factory(seconds,expired);self.timer.daemon=True;self.timer.start()
 def suspend(self):
  with self.lock:
   self.serial+=1
   if self.timer:self.timer.cancel()
   self.timer=None
 def close(self):
  with self.lock:self.closed=True;self.suspend()
