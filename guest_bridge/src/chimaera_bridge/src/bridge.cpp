#include <chrono>
#include <memory>
#include <string>

#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>

#include <gem5/m5ops.h>
#include <m5_mmap.h>

using namespace std::chrono_literals;

class Bridge : public rclcpp::Node
{
    public:
        Bridge()
            : Node("bridge"), count_(0)
        {
            publisher_ = create_publisher<std_msgs::msg::String>("chatter", 10);

            m5op_addr = 0xFFFF0000;   // x86 magic address
            map_m5_mem();
            timer_ = create_wall_timer(500ms, [this]() { publish_message(); });
        }

        ~Bridge()
        {
            unmap_m5_mem();
        }

    private:
        union size_msg_t {
            char c[8];
            uint64_t l;
        };

        void publish_message()
        {
            auto message = std_msgs::msg::String();
            size_msg_t msg;
            msg.l = 0;
            msg.l = count_;
            uint64_t res = m5_chimaera_send_addr(msg.c, 8);
            (void)res;
            message.data = "Hello, world! " + std::to_string(count_++);
            RCLCPP_INFO(get_logger(), "Publishing: '%s'", message.data.c_str());
            publisher_->publish(message);
        }

        rclcpp::TimerBase::SharedPtr timer_;
        rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher_;
        size_t count_;
};

int main(int argc, char * argv[])
{
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<Bridge>());
    rclcpp::shutdown();
    return 0;
}
