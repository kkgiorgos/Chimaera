#include "wall_follow_benchmark/core.hpp"
#include <iostream>
#include <iomanip>
int main() {
  size_t n; double a,da,lo,hi;wall_follow::Parameters p;
  std::cin>>n>>a>>da>>lo>>hi>>p.control_hz>>p.target_distance>>p.speed>>p.kp>>p.heading_gain>>p.max_yaw_rate>>p.front_stop>>p.scan_timeout>>p.beam_stride>>p.sector_start>>p.sector_end>>p.min_points>>p.fit_threshold;
  std::vector<float> ranges(n);std::string value;
  for(auto& r:ranges) {std::cin>>value;r=std::stof(value);}
  p.validate();auto c=wall_follow::command(ranges,a,da,lo,hi,p);
  std::cout<<std::setprecision(17)<<c.speed<<' '<<c.yaw<<' '<<c.distance<<' '<<c.heading<<' '<<c.state<<' '<<c.clearance;
}
