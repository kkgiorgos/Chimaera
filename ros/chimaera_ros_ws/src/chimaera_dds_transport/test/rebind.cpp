#include <chimaera_dds_transport/router.hpp>
#include <gem5/chimaera_rtps_rebind.hh>
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/u_int64.hpp>
#include <sys/wait.h>
#include <unistd.h>
#include <filesystem>
#include <fstream>
#include <thread>
using namespace std::chrono_literals;
namespace fs=std::filesystem;
int child(const std::string& role,const std::string& directory) {
  const bool guest=role=="guest";
  setenv("CHIMAERA_DDS_SOCKET",(directory+(guest?"/guest":"/host")).c_str(),1);
  setenv("CHIMAERA_DDS_SIDE",guest?"guest":"host",1);
  setenv("CHIMAERA_DDS_ROUTES",(directory+"/routes.json").c_str(),1);
  setenv("ROS_DOMAIN_ID","216",1);setenv("ROS_LOCALHOST_ONLY","1",1);
  setenv("RMW_IMPLEMENTATION","rmw_fastrtps_cpp",1);setenv("ROS_LOG_DIR",directory.c_str(),1);
  rclcpp::init(0,nullptr);auto node=std::make_shared<rclcpp::Node>("rebind_"+role);
  bool old_seen=false,retired=false,complete=false;
  auto qos=rclcpp::QoS(4).reliable();
  auto pub=guest?rclcpp::Publisher<std_msgs::msg::UInt64>::SharedPtr{}:
    node->create_publisher<std_msgs::msg::UInt64>("/request",qos);
  auto sub=!guest?rclcpp::Subscription<std_msgs::msg::UInt64>::SharedPtr{}:
    node->create_subscription<std_msgs::msg::UInt64>("/request",qos,[&](std_msgs::msg::UInt64::ConstSharedPtr v){
      if(v->data==1){old_seen=true;std::ofstream(directory+"/matched").put('1');}
      if(v->data==2&&retired){complete=true;std::ofstream(directory+"/reconnected").put('1');}
    });
  auto reply=guest?node->create_publisher<std_msgs::msg::UInt64>("/reply",qos):
    rclcpp::Publisher<std_msgs::msg::UInt64>::SharedPtr{};
  auto reply_sub=guest?rclcpp::Subscription<std_msgs::msg::UInt64>::SharedPtr{}:
    node->create_subscription<std_msgs::msg::UInt64>("/reply",qos,[](std_msgs::msg::UInt64::ConstSharedPtr){});
  const auto deadline=std::chrono::steady_clock::now()+12s;
  while(std::chrono::steady_clock::now()<deadline&&!complete){
    rclcpp::spin_some(node);
    if(guest&&old_seen&&!sub->get_publisher_count()&&!reply->get_subscription_count()){
      retired=true;std::ofstream(directory+"/retired").put('1');
    }
    if(reply&&reply->get_subscription_count()) {std_msgs::msg::UInt64 v;v.data=42;reply->publish(v);}
    if(pub&&pub->get_subscription_count()){
      std_msgs::msg::UInt64 v;v.data=role=="host1"?1:2;pub->publish(v);
    }
    std::this_thread::sleep_for(5ms);
  }
  rclcpp::shutdown();return guest&&!complete?1:0;
}
int main(int argc,char**argv){
  if(argc==3)return child(argv[1],argv[2]);
  const bool omit_dispose=argc==2&&std::string(argv[1])=="--omit-dispose";
  const auto directory=(fs::temp_directory_path()/("chimaera-rebind-"+std::to_string(getpid()))).string();
  fs::create_directory(directory);
  std::ofstream(directory+"/routes.json")<<R"({"version":1,"topics":[{"host_topic":"/request","guest_topic":"/request","type":"std_msgs/msg/UInt64","direction":"host_to_guest"},{"host_topic":"/reply","guest_topic":"/reply","type":"std_msgs/msg/UInt64","direction":"guest_to_host"}]})";
  auto spawn=[&](const char*role){auto pid=fork();if(!pid){execl(argv[0],argv[0],role,directory.c_str(),nullptr);_exit(127);}return pid;};
  auto first=spawn("host1"),guest_pid=spawn("guest");pid_t second=-1;
  bool disposed=false;chimaera_rtps_rebind::Peers peers;
  chimaera_dds::Router host(directory+"/host",1),guest(directory+"/guest",2);
  const auto deadline=std::chrono::steady_clock::now()+(omit_dispose?1s:15s);
  while(std::chrono::steady_clock::now()<deadline&&!fs::exists(directory+"/reconnected")){
    while(auto packet=host.take()){
      std::vector<uint8_t> bytes(packet->data(),packet->data()+packet->size());
      const auto pid=chimaera_rtps_rebind::word(bytes.data()+16);
      if(!disposed){chimaera_rtps_rebind::remember(bytes,peers);guest.receive(std::move(*packet));}
      else if(pid==uint32_t(second))guest.receive(std::move(*packet));
    }
    while(auto packet=guest.take())host.receive(std::move(*packet));
    if(!disposed&&fs::exists(directory+"/matched")&&!peers.empty()){
      kill(first,SIGKILL);waitpid(first,nullptr,0);first=-1;
      if(!omit_dispose)for(const auto&[prefix,peer]:peers){auto bytes=chimaera_rtps_rebind::dispose(prefix,peer);guest.receive(bytes.data(),bytes.size());}
      disposed=true;
    }
    if(disposed&&second<0&&fs::exists(directory+"/retired"))second=spawn("host2");
    std::this_thread::sleep_for(1ms);
  }
  const bool pass=fs::exists(directory+"/retired")&&fs::exists(directory+"/reconnected");
  for(auto pid:{first,second,guest_pid})if(pid>0){kill(pid,SIGKILL);waitpid(pid,nullptr,0);}
  std::fprintf(stderr,"SPDP peers=%zu disposed=%d retired=%d reconnected=%d\n",peers.size(),disposed,int(fs::exists(directory+"/retired")),int(fs::exists(directory+"/reconnected")));
  return omit_dispose ? (!fs::exists(directory+"/retired") && disposed ? 0 : 1) : (pass?0:1);
}
