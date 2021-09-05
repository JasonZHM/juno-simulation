import h5py as h5
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as colors
from matplotlib.backends.backend_pdf import PdfPages

from utils import polar_from_xyz

# constants
Ri = 17.71e3 # inner radius / mm
Ro = 19.5e3 # outer radius / mm
Volume_i = 4 / 3 * np.pi * Ri ** 3 # volume of LS

NumBins_Density = 30
NumBins_PETime = 50
NumBins_Probe = 50

# 该类在测试时会用到，请不要私自修改函数签名，后果自负
class Drawer:
    def __init__(self, data, geo):
        self.simtruth = data["ParticleTruth"]
        self.petruth = data["PETruth"]
        self.geo = geo["Geometry"]

        self.N_vertices = len(data['ParticleTruth']) # total num of vertices
        self.rho0 = self.N_vertices / Volume_i # average density / mm^-3

    def draw_vertices_density(self, fig, ax):
        '''
        draw density of vertices as a function of radius:
        density = density(radius)
        '''
        x = np.array(self.simtruth['x'])
        y = np.array(self.simtruth['y'])
        z = np.array(self.simtruth['z'])

        # radius
        r = np.sqrt(x ** 2 + y ** 2 + z ** 2)

        # extract histogram statistics
        n, bins, patches = ax.hist(r, bins=NumBins_Density, density=False)
        ax.cla()

        # plot
        ax.set_title(r'Volume Density of Vertices $\rho(r)$')
        ax.set_xlabel(r'Radius from Origin $r$ / $R_{LS}$')
        ax.set_xlim(0, Ri)
        ax.set_xticks(np.linspace(0, Ri, 11))
        ax.set_xticklabels(['%.1f' % (i / 10) for i in range(11)])

        ax.set_ylabel(
            r'Volume Density of Vertices $\rho(r)$ / $\rho_0 = {:.2f} \times 10^{{{:.0f}}} mm^{{-3}}$'
            .format(
                *map(float, 
                ('%.2e'%self.rho0).split('e'))
                ))
        ax.set_ylim(0, 2 * self.rho0)
        ax.set_yticks(np.linspace(0, 2 * self.rho0, 5))
        ax.set_yticklabels(['%.1f' % (2 * i / 4) for i in range(5)])
        
        # density = dN / dV 
        # dV = 4/3*pi*d(r^3)
        deltaVs = 4 / 3 * np.pi * (bins[1:] ** 3 - bins[:-1] ** 3)
        avgbins = (bins[:-1] + bins[1:]) / 2
        ax.scatter(avgbins, n / deltaVs, color='red')
        ax.plot(avgbins, n / deltaVs, color='red')
        

    def draw_pe_hit_time(self, fig, ax):
        '''
        draw histogram of PE hit time:
        #PE = #PE(time)
        '''
        time = np.array(self.petruth['PETime'])
        maxtime = np.max(time)

        # plot
        ax.set_title(r'Histogram of PE Hit Time')
        ax.set_xlabel(r'Hit Time $t$ / $ns$')
        ax.set_xlim(0, maxtime)
        ax.set_xticks(np.linspace(0, maxtime, 11))
        ax.set_xticklabels(['%.0f' % i for i in np.linspace(0, maxtime, 11)])

        ax.set_ylabel(r'Number of PE Hit')

        ax.hist(time, bins=NumBins_PETime, density=False)
        

    def draw_probe(self, fig, ax):
        '''
        draw probe function
        average over all PMTs (Channels)
        probe = probe(theta, r)
        '''
        pt = self.simtruth
        pet = self.petruth
        geo = self.geo

        Events, Events_i = np.unique(pet['EventID'], return_inverse=True)
        Channels, Channels_i = np.unique(pet['ChannelID'], return_inverse=True)
        
        # replace ChannelID with corresponding geo
        geo_Channels_i = np.array([np.where(geo['ChannelID']==a)[0][0] for a in Channels])
        pet_geo_i = geo_Channels_i[Channels_i]
        pet_geo = np.stack([geo['theta'][pet_geo_i], geo['phi'][pet_geo_i]], -1)

        # replace EventID with corresponding xyz
        xyz_Event_i = np.array([np.where(pt['EventID']==a)[0][0] for a in Events])
        pet_xyz_i = xyz_Event_i[Events_i]
        pet_xyz = np.stack([pt['x'][pet_xyz_i], pt['y'][pet_xyz_i], pt['z'][pet_xyz_i]], -1)

        # raplace xyz, geo with polar coordinates
        pet_polar = np.stack(polar_from_xyz(Ro, pet_geo[:, 0], pet_geo[:, 1], pet_xyz[:, 0], pet_xyz[:, 1], pet_xyz[:, 2]), -1)

        N_PE = len(pet_polar)
        
        # extract histogram statistics
        # density=True: h = d#PE/(#PE dr dtheta)
        h, xedges, yedges, im = ax.hist2d(pet_polar[:, 0], pet_polar[:, 1], NumBins_Probe, range=[[0, np.pi-1e-2], [0, Ri]], density=True)
        ax.cla()

        # expand theta from [0, pi] to [0, 2pi]
        xedges, yedges = xedges[1:], yedges[1:]
        xedges_double = np.hstack([xedges, xedges + np.pi])
        h_double = np.hstack([h, np.fliplr(h)])

        ThetaMesh, RMesh = np.meshgrid(xedges_double, yedges)

        # plot heatmap

        ax.set_title(r'Heatmap of the Probe Function $Prob(R, \theta)$')

        # d#PE/d#Vertices = d#PE/dV * dV/d#Vertices = d#PE/(dV rho0) = d#PE/(2pi r sin(theta) dr dtheta rho0)
        pcm = ax.pcolormesh(ThetaMesh, RMesh, h_double / self.rho0 * N_PE, shading='auto', norm=colors.LogNorm())

        fig.colorbar(pcm, label='Expected Number of PE per Vertex')
        

if __name__ == "__main__":
    import argparse

    # 处理命令行
    parser = argparse.ArgumentParser()
    parser.add_argument("ipt", type=str, help="Input simulation data")
    parser.add_argument("-g", "--geo", dest="geo", type=str, help="Geometry file")
    parser.add_argument("-o", "--output", dest="opt", type=str, help="Output file")
    args = parser.parse_args()

    # 读入模拟数据
    data = h5.File(args.ipt, "r")
    geo = h5.File(args.geo, "r")
    drawer = Drawer(data, geo)

    # 画出分页的 PDF
    with PdfPages(args.opt) as pp:
        print('Printing Vertex Density')
        fig = plt.figure()
        ax = fig.add_subplot(1, 1, 1)
        drawer.draw_vertices_density(fig, ax)
        pp.savefig(figure=fig)

        print('Printing PETime')
        fig = plt.figure()
        ax = fig.add_subplot(1, 1, 1)
        drawer.draw_pe_hit_time(fig, ax)
        pp.savefig(figure=fig)

        # print('Printing Probe')
        # # Probe 函数图像使用极坐标绘制，注意 x 轴是 theta，y 轴是 r
        # fig = plt.figure()
        # ax = fig.add_subplot(1, 1, 1, projection="polar", theta_offset=np.pi / 2)
        # drawer.draw_probe(fig, ax)
        # pp.savefig(figure=fig)
