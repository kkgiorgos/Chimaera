#include <chimaera_ros_bridge/config.hpp>
#include <iostream>

int main(int argc, char ** argv)
{
  if (argc != 2) {return 2;}
  try {
    const auto routes = chimaera_ros_bridge::load_config(argv[1]);
    std::cout << "Validated " << routes.size() << " topic routes\n";
    return 0;
  } catch (const std::exception & error) {
    std::cerr << "Invalid routes: " << error.what() << '\n';
    return 2;
  }
}
