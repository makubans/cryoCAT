import decimal
import emfile
import numpy as np
import os
import pandas as pd
import subprocess
import re
import warnings

from cryocat.exceptions import UserInputError
from cryocat import cryomap
from cryocat import geom
from cryocat import starfileio
from cryocat import cryomask
from cryocat import mathutils

from math import ceil
from matplotlib import pyplot as plt

from scipy.spatial import KDTree
from scipy.spatial.transform import Rotation as rot


class Motl:
    # Motl module example usage
    #
    # Initialize a Motl instance from an emfile
    #   `motl = Motl.load(’path_to_em_file’)`
    # Run clean_by_otsu and write the result to a new file
    #   `motl.clean_by_otsu(4, histogram_bin=20).write_to_emfile('path_to_output_em_file')`
    # Run class_consistency on multiple Motl instances
    #   `motl_intersect, motl_bad, cl_overlap = Motl.class_consistency(Motl.load('emfile1', 'emfile2', 'emfile3'))`
    motl_columns = [
        "score",
        "geom1",
        "geom2",
        "subtomo_id",
        "tomo_id",
        "object_id",
        "subtomo_mean",
        "x",
        "y",
        "z",
        "shift_x",
        "shift_y",
        "shift_z",
        "geom3",
        "geom4",
        "geom5",
        "phi",
        "psi",
        "theta",
        "class",
    ]

    def __init__(self, motl_df=None):
        if motl_df is not None:
            if self.check_df_correct_format(motl_df):
                self.df = motl_df
            else:
                raise ValueError("Provided pandas.DataFrame does not have correct format.")
        else:
            self.df = Motl.create_empty_motl_df()

    def adapt_to_trimming(self, trim_coord_start, trim_coord_end):
        """The adapt_to_trimming function takes in the trim_coord_start and trim_coord_end values, which are the
        coordinates used for trimming the tomogram, and changes particle coordinates to correspond to the trimmed
        tomogram. The particles from the trimmed area are removed.

        Parameters
        ----------
        trim_coord_start : numpy.ndarray
            Starting coordinates of the trimming used on the tomogram. The order of coordinates is x, y, z.
        trim_coord_end : numpy.ndarray
            Ending coordinates of the trimming used on the tomogramThe order of coordinates is x, y, z.

        Returns
        -------
        None

        Notes
        -----
        Currently does not work for multiple trimming values (specified e.g. by tomo_id), only one set of coordinates
        is supported and applied to all particles.

        This method modifies the `df` attribute of the object.

        """

        trimvol_coord = np.asarray(trim_coord_start) - 1
        tdim = np.asarray(trim_coord_end) - trimvol_coord
        self.df.loc[:, ["x", "y", "z"]] = self.df.loc[:, ["x", "y", "z"]] - np.tile(
            trimvol_coord, (self.df.shape[0], 1)
        )
        self.df = self.df.loc[~((self.df["x"] < 1.0) | (self.df["y"] < 1.0) | (self.df["z"] < 1.0)), :]
        self.df = self.df.loc[
            ~((self.df["x"] > tdim[0]) | (self.df["y"] > tdim[1]) | (self.df["z"] > tdim[2])),
            :,
        ]

    def apply_rotation(self, rotation):
        """The apply_rotation function applies a rotation to the angles in the DataFrame.

        Parameters
        ----------
        rotation : `scipy.spatial.transform._rotation.Rotation`
            A scipy rotation object representing the rotation.

        Returns
        -------
        None

        Notes
        -----
        This method modifies the `df` attribute of the object.

        """

        angles = self.df.loc[:, ["phi", "theta", "psi"]].to_numpy()

        angles_rot = rot.from_euler("zxz", angles, degrees=True)
        final_rotation = angles_rot * rotation
        angles = final_rotation.as_euler("zxz", degrees=True)
        self.df.loc[:, ["phi", "theta", "psi"]] = angles

    def assign_column(self, input_df, column_pairs):
        """The assign_column function takes a dataframe and a dictionary of column pairs.
        The function then iterates through the dictionary, checking if the paired key is in
        the input_df columns. If it is, it assigns that column to the em_key in self.df.

        Parameters
        ----------
        input_df : pandas.DataFrame
            The input DataFrame containing the columns to be assigned.
        column_pairs : dict
            A dictionary mapping the column names in self.df to the corresponding column names in input_df.

        Returns
        -------
        None

        Examples
        --------
        >>> assign_column(input_df, {'tomo_id': 'input_df_key1', 'subtomo_id': 'input_df_key2'})

        """

        for em_key, paired_key in column_pairs.items():
            if paired_key in input_df.columns:
                self.df[em_key] = pd.to_numeric(input_df[paired_key])

    def clean_by_distance(self, distnace_in_voxels, feature_id, metric_id="score", score_cut=0):
        """Cleans `df` by removing particles closer than a given distnace threshold (in voxels).

        Parameters
        ----------
        distnace_in_voxels : float
            The distance cutoff in voxels.
        feature_id : str
            The ID of the feature by which the particles are grouped before cleaning.
        metric_id : str, default='score'
            The ID of the metric to decide which particles to keep. Defaults to "score". The particle with the greater
            value is kept.
        score_cut : float, default=0.0
            The cutoff value for the metric. Entries with metric values below this cutoff
            will be removed. Defaults to 0.0.

        Returns
        -------
        None

        Notes
        -----
        This method modifies the `df` attribute of the object.

        """

        # Distance cutoff (pixels)
        d_cut = distnace_in_voxels

        # Parse tomograms
        features = np.unique(self.get_features(feature_id))

        # Initialize clean motl
        cleaned_df = pd.DataFrame()

        # Loop through and clean
        for f in features:
            # Parse tomogram
            feature_m = self.get_motl_subset(f, feature_id=feature_id, reset_index=False)
            n_temp_motl = feature_m.df.shape[0]

            # Parse positions
            pos = feature_m.get_coordinates()

            # Parse scores
            temp_scores = feature_m.df[metric_id].values

            # Sort scores
            sort_idx = np.argsort(temp_scores)[::-1]

            # Temporary keep index
            temp_keep = np.ones((n_temp_motl,), dtype=bool)
            temp_keep[temp_scores < score_cut] = False

            # Loop through in order of score
            for j in sort_idx:
                if temp_keep[j]:
                    # Calculate distances
                    dist = geom.point_pairwise_dist(pos[j, :], pos)

                    # Find cutoff
                    d_cut_idx = dist < d_cut

                    # Keep current entry
                    d_cut_idx[j] = False

                    # Remove other entries
                    temp_keep[d_cut_idx] = False

            # Add entries to main list
            cleaned_df = pd.concat((cleaned_df, feature_m.df.iloc[temp_keep, :]), ignore_index=True)

        self.df = cleaned_df

    def clean_by_otsu(self, feature_id, histogram_bin=None):
        """Clean the DataFrame by applying Otsu's thresholding algorithm on the scores.

        Parameters
        ----------
        feature_id : str
            The feature ID to be used to group particles for cleaning.
        histogram_bin : int, optional
            The number of bins for the histogram. If not provided, a default value will be used based on the feature ID.
            Defaults to None.

        Returns
        -------
        None

        Notes
        -----
        This method modifies the `df` attribute of the object.

        Raises
        ------
        UserInputError
        If the selected feature ID does not correspond to either "tomo_id" or "object_id",
        and histogram_bin is not specified.

        """

        tomos = self.get_unique_values(feature_id)
        cleaned_motl = self.__class__.create_empty_motl_df()

        if histogram_bin:
            hbin = histogram_bin
        else:
            if feature_id == "tomo_id":
                hbin = 40
            elif feature_id == "object_id":
                hbin = 30
            else:
                raise UserInputError(
                    f"The selected feature ({feature_id}) does not correspond either to tomo_id, nor to"
                    f"object_id. You need to specify the histogram_bin."
                )

        for t in tomos:  # if feature == object_id, tomo_id needs to be used too
            tm = self.df.loc[self.df["tomo_id"] == t]
            features = tm.loc[:, feature_id].unique()

            for f in features:
                fm = tm.loc[tm[feature_id] == f]
                bin_counts, bin_centers, _ = plt.hist(fm.loc[:, "score"], bins=hbin)  # TODO check if correct
                bn = mathutils.otsu_threshold(bin_counts)
                cc_t = bin_centers[bn]
                fm = fm.loc[fm["score"] >= cc_t]

                cleaned_motl = pd.concat([cleaned_motl, fm])

        self.df = cleaned_motl.reset_index(drop=True)

    def convert_to_motl(self, input_df):
        """Abstract method implemented only within child classes.

        Parameters
        ----------
        input_df : pandas.DataFrame


        Returns
        -------
        None

        Raises
        ------
        UserInputError
        In case this function is called from `Motl` - the provided input_df should be in correct format, there is
        no conversion possible.

        """

        raise ValueError("Provided motl does not have correct format.")

    @staticmethod
    def create_empty_motl_df():
        """Creates an empty DataFrame with the columns defined in :attr:`cryocat.cryomotl.Motl.motl_columns`.

        Parameters
        ----------
        None

        Returns
        -------
        pandas.DataFrame
            An empty DataFrame with the columns defined in :attr:`cryocat.cryomotl.Motl.motl_columns`.

        """

        empty_motl_df = pd.DataFrame(
            columns=Motl.motl_columns,
            dtype=float,
        )
        return empty_motl_df

    @staticmethod
    def check_df_correct_format(input_df):
        """Check if the input DataFrame has the correct format.

        Parameters
        ----------
        input_df : pandas.DataFrame
            The DataFrame to be checked.

        Returns
        -------
        bool
            True if the input DataFrame has the correct format, False otherwise.

        """

        if sorted(Motl.motl_columns) == sorted(input_df.columns):
            return True
        else:
            return False

    def check_df_type(self, input_motl):
        """Checks the type of the input dataframe and assigns it to the class attribute 'df' if it is in the
        correct format. If it is not in the correct format it tries to convert it.

        Parameters
        ----------
        input_motl : pandas.DataFrame
            The input dataframe to be checked.

        Returns
        -------
        None

        Notes
        -----
        This function is meant to be called by child classes as the possible conversion is not implemented within
        this class.

        """

        if Motl.check_df_correct_format(input_motl):
            self.df = input_motl.copy()
            self.df.reset_index(inplace=True, drop=True)
        else:
            self.convert_to_motl(input_motl)

    def fill(self, input_dict):
        """The fill function is used to fill in the values of a column or columns
        in the starfile. The input_dict argument should be a dictionary with keys
        that are either column names, coord, angles, shifts.

        Parameters
        ----------
        input_dict : dict
            Dictionary with keys from :attr:`cryocat.cryomotl.Motl.motl_columns` and new values to be
            assigned. Three special keys are allowed: coord (which will assign values to x, y, z columns), angles
            (which will assign values to phi, theta, psi), and shifts (which will assign values to shift_x, shift_y,
            shift_z).

        Notes
        -----
        This method modifies the `df` attribute of the object.

        Returns
        -------
        None

        """
        for key, value in input_dict.items():
            if key in self.df.columns:
                self.df[key] = value
            elif key == "coord":
                self.df[["x", "y", "z"]] = value
            elif key == "angles":
                self.df[["phi", "theta", "psi"]] = value
            elif key == "shifts":
                self.df[["shift_x", "shift_y", "shift_z"]] = value

    def flip_handedness(self, tomo_dimensions=None):
        """Flip the handedness of the particles in the motl.

        Parameters
        ----------
        tomo_dimensions : str or numpy.ndarray, optional
            Dimensions of tomograms in the motl. If not provided, only the orientation is changed. For specification on
            tomo_dimensions format see :meth:`cryocat.geom.load_dimensions`. Defaults to None.

        Notes
        -----
        Orientation is flipped by changing the sign of the theta angle (following ZXZ convention of Euler angles).

        The position flip is performed by subtracting the z-coordinate from the maximum z-dimension and adding 1.

        This method modifies the `df` attribute of the object.

        Returns
        -------
        None

        Examples
        --------
        >>> flip_handedness(tomo_dimensions="dimensions.txt")

        """
        self.df.loc[:, "theta"] = -self.df.loc[:, "theta"]

        # Position flip
        if tomo_dimensions is not None:
            dims = geom.load_dimensions(tomo_dimensions)
            if dims.shape == (1, 3):
                z_dim = float(dims["z"]) + 1
                self.df.loc[:, "z"] = z_dim - self.df.loc[:, "z"]
            else:
                tomos = dims["tomo_id"].unique()
                for t in tomos:
                    z_dim = float(dims[dims["tomo_id"] == t, "z"]) + 1
                    self.df.loc[self.df["tomo_id"] == t, "z"] = z_dim - self.df.loc[self.df["tomo_id"] == t, "z"]

    def get_angles(self, tomo_number=None):
        """This function takes in a tomo_number and returns the angles of all particles in that
        tomogram. If no tomo_number is given, it will return the angles of all particles.

        Parameters
        ----------
        tomo_number : int, optional
            The tomogram number. If not provided, all angles will be returned. Defaults to None.

        Returns
        -------
        numpy.ndarray
            An array of angles in the format [phi, theta, psi] (corresponds to the zxz Euler convention).

        """

        if tomo_number is None:
            angles = self.df.loc[:, ["phi", "theta", "psi"]].values
        else:
            angles = self.df.loc[self.df.loc[:, "tomo_id"] == tomo_number, ["phi", "theta", "psi"]].values

        return angles

    def get_coordinates(self, tomo_number=None):
        """This function takes in a tomo_number and returns the coordinates of all particles in that
        tomogram. If no tomo_number is given, it will return the coordinates of all particles. The coordinates are
        computes as x + shift_x, y + shift_y, z + shift_z.

        Parameters
        ----------
        tomo_number : int, optional
            The tomogram number. If not provided, all coordinates will be returned. Defaults to None.

        Returns
        -------
        numpy.ndarray
            3D array of coordinates in the format [x + shift_x, y + shift_y, z + shift_z].

        """
        if tomo_number is None:
            coord = self.df.loc[:, ["x", "y", "z"]].values + self.df.loc[:, ["shift_x", "shift_y", "shift_z"]].values
        else:
            coord = (
                self.df.loc[self.df.loc[:, "tomo_id"] == tomo_number, ["x", "y", "z"]].values
                + self.df.loc[
                    self.df.loc[:, "tomo_id"] == tomo_number,
                    ["shift_x", "shift_y", "shift_z"],
                ].values
            )

        return coord

    def get_max_number_digits(self, feature_id="tomo_id"):
        """This function returns the maximum number of digits in the column specified by feature_id.

        Parameters
        ----------
        feature_id : str, default="tomo_id"
            Specify the column name of the feature to get the max digits. Defaults to "tomo_id".

        Returns
        -------
        int
            The maximum number of digits in the column specified by feature_id.

        """
        max_tomo_id = self.df[feature_id].max()
        return len(str(max_tomo_id))

    def get_rotations(self, tomo_number=None):
        """The get_rotations function returns rotations for all particles.

        Parameters
        ----------
        tomo_number : int, optional
            The tomogram number. If not provided, all rotations will be returned. Defaults to None.

        Returns
        -------
        list
            List of rotations (with type `scipy.spatial.transform._rotation.Rotation`) for all particles.

        """
        angles = self.get_angles(tomo_number)
        rotations = rot.from_euler("zxz", angles, degrees=True)

        return rotations

    def get_barycentric_motl(self, idx, nn_idx):
        """Returns a new Motl object with coordinates corresponding to the barycentric coordinates of the particles
        (speficied by their indices within idx) and their nearest neigbors (specified by their indices within nn_idx).

        Parameters
        ----------
        idx : array-like
            Array (type `int`) with indices of particles to be used for the analysis.
        nn_idx : array-like
            Array (type `int`) with indices of nearest neigbors to be used to compute the barycentric coordinates.
            The dimensions are (N, x) where N corresponds to the number of particles (same as in idx) and x is the
            number of nearest neigbors. If x equals 1, the barycentric coordinates are computed between two points
            (specified by idx and nn_idx), if x equals 2, the barycentric coordinates correspond to the barycentric
            coordinates of a triangle specified by the 3 points (one from idx, two from nn_idx). In theory, x can be
            arbitrarily large.

        Returns
        -------
        :class:`Motl`
            A new Motl object with coordinates corresponding to the barycentric centers

        Notes
        -----
        TODO: Move the geometry specific computation to the :mod:`cryocat.geom`.

        """

        coord1 = self.get_coordinates()[idx, :]
        new_coord = coord1.copy()

        n_vertices = nn_idx.shape[1]

        coord2 = np.zeros((nn_idx.shape[0], 3, n_vertices))

        for i in range(n_vertices):
            coord2[:, :, i] = self.get_coordinates()[nn_idx[:, i], :]
            new_coord += coord2[:, :, i]

        new_coord = new_coord / (n_vertices + 1)
        print(new_coord[0, :])
        c_coord = new_coord - coord1

        angles = self.df[["phi", "theta", "psi"]].values
        angles = angles[idx, :]

        """
        norm_start_vec = np.linalg.norm([1, 0, 0])
        norm_diff_vecs = np.linalg.norm(rotated_coord, axis=1)
        cos_angles = np.sum([1, 0, 0] * rotated_coord, axis=1) / (
            norm_start_vec * norm_diff_vecs
        )
        phi_rotation = np.rad2deg(np.arccos(cos_angles))
        phi_rotation = np.rad2deg(np.arctan(c_coord[:, 1] / c_coord[:, 0]))
        """
        # starting frame created from the first orientation
        w1 = geom.euler_angles_to_normals(angles[0, :])
        w2 = c_coord[0, :] / np.linalg.norm(c_coord[0, :])
        w3 = np.cross(w1, w2)
        w3 = (w3 / np.linalg.norm(w3)).reshape(
            3,
        )
        w_base_mat = np.asarray([w1.reshape((3,)), w2, w3]).T
        print(w1, w2, w3)
        print(w_base_mat)

        v1 = geom.euler_angles_to_normals(angles)

        rot_angles = np.zeros(angles.shape)

        for i in range(1, angles.shape[0]):
            v2 = c_coord[i, :] / np.linalg.norm(c_coord[i, :])
            v3 = np.cross(v1[i, :], v2)
            v3 = (v3 / np.linalg.norm(v3)).reshape(
                3,
            )
            v_base_mat = np.asarray([v1[i, :].reshape((3,)), v2, v3])
            final_mat = np.matmul(w_base_mat, v_base_mat)
            final_rot = rot.from_matrix(final_mat)
            rot_angles[i, :] = final_rot.as_euler("zxz", degrees=True)

        new_motl_df = Motl.create_empty_motl_df()

        new_motl_df[["x", "y", "z"]] = new_coord
        new_motl_df[["shift_x", "shift_y", "shift_z"]] = 0.0
        new_motl_df["tomo_id"] = self.df["tomo_id"].values[idx]
        new_motl_df["object_id"] = self.df["object_id"].values[idx]
        new_motl_df["class"] = 1
        new_motl_df["subtomo_id"] = range(1, new_motl_df.shape[0] + 1)
        # new_motl_df[["phi", "psi", "theta"]] = self.df[["phi", "psi", "theta"]].values[
        #    idx
        # ]
        new_motl_df[["phi", "theta", "psi"]] = rot_angles

        new_motl = Motl(new_motl_df)
        new_motl.update_coordinates()

        return new_motl

    def get_feature(self, feature_id):
        """Returns the values from the column in self.df specified by feature_id.

        Parameters
        ----------
        feature_id : str
            The column name to get the values for.

        Returns
        -------
        numpy.ndarray
            Values corresponding to the feature_id.

        """
        return self.df.loc[:, feature_id].values

    def get_motl_subset(self, feature_values, feature_id="tomo_id", return_df=False, reset_index=True):
        """Get a subset of the Motl object based on specified feature values.

        Parameters
        ----------
        feature_values : array-like or int
            The feature values to filter the Motl object by.
        feature_id : str default= "tomo_id"
            The name of the feature column to filter by. Defaults to "tomo_id".
        return_df : bool, default=False
            Whether to return the filtered subset as a DataFrame. Defaults to False.
        reset_index : bool, default=True
            Whether to reset the index of the filtered subset. Defaults to True.
        Returns
        -------
        `pandas.DataFrame` or :class:`Motl`
            If return_df is True, returns the filtered subset as a DataFrame. Otherwise, returns a new :class:`Motl`
            object containing the filtered subset.

        Warnings
        --------
        The default value of reset_index was changed from False to True.

        """

        if isinstance(feature_values, list):
            feature_values = np.array(feature_values)
        else:
            feature_values = np.array([feature_values])

        new_df = Motl.create_empty_motl_df()
        for i in feature_values:
            df_i = self.df.loc[self.df[feature_id] == i].copy()
            new_df = pd.concat([new_df, df_i])

        if reset_index:
            new_df = new_df.reset_index(drop=True)

        if return_df:
            return new_df
        else:
            return Motl(motl_df=new_df)

    @classmethod
    def get_motl_intersection(cls, motl1, motl2, feature_id="subtomo_id"):
        """Creates motl intersection of two motls based on feature_id.

        Parameters
        ----------
        motl1 : :class:`Motl`
            First motl.
        motl2 : :class:`Motl`
            Second motl.
        feature_id : str, default="subtomo_id"
            Feature ID to use for intersection. Defaults to "subtomo_id".

        Returns
        -------
        :class:`Motl`
            The intersection (based on feature_id) of two motls.

        """
        m1 = cls.load(motl1.df)
        m2 = cls.load(motl2.df)

        s1 = m1.df.merge(m2.df[feature_id], how="inner")

        if s1.shape[0] == 0:
            warnings.warn("The intersection of the two motls is empty.")

        return cls(s1.reset_index(drop=True))

    def get_relative_position(self, idx, nn_idx):
        """Returns a new Motl object with coordinates corresponding to the center between the particles
        (speficied by their indices within idx) and their nearest neigbors (specified by their indices within nn_idx).

        Parameters
        ----------
        idx : array-like
            Indices of particles to be used for the analysis.
        nn_idx : array-like
            Indices of nearest neigbors of particles specified in idx.

        Returns
        -------
        :class:`Motl`
            A new Motl object with coordinates corresponding to the center between the particles and their nearest
            neighbors.

        Notes
        -----
        TODO: Move the geometry specific computation to the :mod:`cryocat.geom`.

        """
        coord1 = self.get_coordinates()[idx, :]
        coord2 = self.get_coordinates()[nn_idx, :]
        print(coord1.shape, coord2.shape)
        c_coord = coord2 - coord1

        angles = self.df[["phi", "theta", "psi"]].values
        angles = angles[idx, :]
        rot_coord = rot.from_euler("zxz", angles=angles, degrees=True)
        rotated_coord = rot_coord.apply(c_coord)

        """
        norm_start_vec = np.linalg.norm([1, 0, 0])
        norm_diff_vecs = np.linalg.norm(rotated_coord, axis=1)
        cos_angles = np.sum([1, 0, 0] * rotated_coord, axis=1) / (
            norm_start_vec * norm_diff_vecs
        )
        phi_rotation = np.rad2deg(np.arccos(cos_angles))
        phi_rotation = np.rad2deg(np.arctan(c_coord[:, 1] / c_coord[:, 0]))
        """
        # starting frame created from the first orientation
        w1 = geom.euler_angles_to_normals(angles[0, :])
        w2 = c_coord[0, :] / np.linalg.norm(c_coord[0, :])
        w3 = np.cross(w1, w2)
        w3 = (w3 / np.linalg.norm(w3)).reshape(
            3,
        )
        w_base_mat = np.asarray([w1.reshape((3,)), w2, w3]).T
        print(w1, w2, w3)
        print(w_base_mat)

        v1 = geom.euler_angles_to_normals(angles)

        rot_angles = np.zeros(angles.shape)

        for i in range(1, angles.shape[0]):
            v2 = c_coord[i, :] / np.linalg.norm(c_coord[i, :])
            v3 = np.cross(v1[i, :], v2)
            v3 = (v3 / np.linalg.norm(v3)).reshape(
                3,
            )
            v_base_mat = np.asarray([v1[i, :].reshape((3,)), v2, v3])
            final_mat = np.matmul(w_base_mat, v_base_mat)
            final_rot = rot.from_matrix(final_mat)
            rot_angles[i, :] = final_rot.as_euler("zxz", degrees=True)

        new_coord = (coord1 + coord2) / 2.0
        print(new_coord[0, :])
        new_motl_df = Motl.create_empty_motl_df()

        new_motl_df[["x", "y", "z"]] = new_coord
        new_motl_df[["shift_x", "shift_y", "shift_z"]] = 0.0
        new_motl_df["tomo_id"] = self.df["tomo_id"].values[idx]
        new_motl_df["object_id"] = self.df["object_id"].values[idx]
        new_motl_df["class"] = 1
        new_motl_df["subtomo_id"] = range(1, new_motl_df.shape[0] + 1)
        # new_motl_df[["phi", "psi", "theta"]] = self.df[["phi", "psi", "theta"]].values[
        #    idx
        # ]
        new_motl_df[["phi", "theta", "psi"]] = rot_angles

        new_motl = Motl(new_motl_df)
        new_motl.update_coordinates()

        return new_motl, rotated_coord

    def get_unique_values(self, feature_id):
        """Get unique values from a specific feature.

        Parameters
        ----------
        feature_id : str
            The ID of the feature.

        Returns
        -------
        numpy.ndarray
            A numpy.ndarray containing the unique values stored in the column feature_id.

        """

        return self.df.loc[:, feature_id].unique()

    @classmethod
    def load(cls, input_motl, motl_type="emmotl"):
        """This function is a factory function that returns an instance of the appropriate Motl class.

        Parameters
        ----------
        input_motl : pandas.DataFrame
            Either path to the motl or pandas.DataFrame in the format corresponding
            to general motl_df or in the format specific to the motl_type.
        motl_type : str, {'emmotl', 'dynamo', 'relion', 'stopgap'}
            A string indicating what type of Motl input should be loaded (emmotl, relion, stopgap, dynamo).
            Defaults to emmotl.

        Returns
        -------
        child of :class:`Motl`
           Subclass of the abstract class :class:`Motl` specified by `motl_type`.

        Raises
        ------
        UserInputError
            In case the motl_type is not supported.

        """
        if motl_type == "emmotl":
            return EmMotl(input_motl)
        elif motl_type == "relion":
            return RelionMotl(input_motl)
        elif motl_type == "stopgap":
            return StopgapMotl(input_motl)
        elif motl_type == "dynamo":
            return DynamoMotl(input_motl)
        else:
            raise UserInputError(f"Provided motl file {input_motl} has format that is currently not supported.")

    def remove_feature(self, feature_id, feature_values):
        """The function removes particles based on their feature (i.e. tomo number).

        Parameters
        ----------
        feature_id : str
            Specify the feature based on which the particles will be removed.
        feature_values : array-like
            Specify which particles should be removed.


        Returns
        -------
        None

        Notes
        -----
        This method modifies the `df` attribute of the object.

        """

        if not isinstance(feature_values, list):
            feature_values = [feature_values]
        for value in feature_values:
            self.df = self.df.loc[self.df[feature_id] != value]

    def renumber_particles(self):
        """This function renumbers the particles in a motl. This is useful when you want to reorder the particles in a
        motl, or if you have deleted some of them and need to renumber the remaining ones. The function takes no

        Parameters
        ----------
        None

        Returns
        -------
        None

        Notes
        -----
        Numbering starts with 1.

        This method modifies the `df` attribute of the object.

        """
        self.df.loc[:, "subtomo_id"] = list(range(1, len(self.df) + 1))

    def scale_coordinates(self, scaling_factor):
        """Scales coordinates (including shifts) by the scaling factor.

        Parameters
        ----------
        scaling_factor : float
            Factor to scale the coordinates.

        Notes
        -----
        This method modifies the `df` attribute of the object.

        Returns
        -------
        None

        """
        for coord in ("x", "y", "z"):
            self.df[coord] = self.df[coord] * scaling_factor
            shift_column = "shift_" + coord
            self.df[shift_column] = self.df[shift_column] * scaling_factor

    def split_by_feature(self, feature_id, write_out=False, output_prefix=""):
        """Splits motl by the feature_id and writes them out.

        Parameters
        ----------
        feature_id : str
            Specify the feature based on which the motl will be split.
        write_out : bool, default=False
            Whether to write out the motls. Defaults to False.
        output_prefix : bool, default=""
            Prefix (including the path) of the motls to be written out. Used only if write_out is True. No
            separation character will be added - it has to be specified as the last character. Defaults to empty `str`.

        Returns
        -------
        list
            List of :class:`Motl` split by given feature_id.

        Warnings
        --------
        This method does not preserve a child class - it always returns :class:`Motl`. Correspondingly, the individual
        motls - if written out - will be in "emmotl" format.

        Notes
        -----
        TODO: Make this function to be class specific.

        """
        uniq_values = self.get_unique_values(feature_id)
        motls = list()

        for value in uniq_values:
            # submotl = self.__class__(self.df.loc[self.df[feature_id] == value])
            submotl = Motl(self.df.loc[self.df[feature_id] == value])
            motls.append(submotl)

            if write_out:
                out_name = f"{output_prefix}{str(int(value))}.em"
                submotl.write_out(out_name)

        return motls

    def write_out(self, output_path, motl_type="emmotl"):
        """Writes out a motl file to the specified output path.

        Parameters
        ----------
        output_path : str
            The path to write the motl file to.
        motl_type : str, {'emmotl', 'dynamo', 'relion', 'stopgap'}
            The type of motl file to write. Defaults to "emmotl".

        Returns
        -------
        None

        Raises
        ------
        UserInputError
            If the provided motl format is not supported.

        """

        if motl_type.lower() == "emmotl":
            EmMotl(self.df).write_out(output_path)
        elif motl_type.lower() == "relion":
            RelionMotl(self.df).write_out(output_path)
        elif motl_type.lower() == "stopgap":
            StopgapMotl(self.df).write_out(output_path)
        elif motl_type.lower() == "dynamo":
            DynamoMotl(self.df).write_out(output_path)
        else:
            raise UserInputError(f"Provided motl file {output_path} has format that is currently not supported.")

    def write_to_model_file(self, feature_id, output_base, point_size, binning=1.0, zero_padding=None):
        """It splits the dataframe based on feature_id and writes them out as mod files (from IMOD). The values in "class"
        column are used to created different objects, the countour is always the same. This function requires IMOD's
        point2model function to exist and being in PATH.

        Parameters
        ----------
        feature_id : str
            Name of the feature (column) to split by.
        output_base : str
            The base for the output files. The final name of each mod file will have a form of
            {output_base}_{feature_id}_{feature_id_value} where the feature_id_value will be pad with zeros.
        point_size : int
            Size of the point that should be used
        binning : float, default=1.0
            Scaling factor to apply to coordinates. Defaults to 1.0 which corresponds to no binning.
        zero_padding : int, optional
            Defines the zero padding for the feature_id_value for the output names (see
            above). In None, the length of the maximum value in feature_id is used. Defaults to None.

        Returns
        -------
        None

        """
        uniq_values = self.get_unique_values(feature_id)
        outpath = f"{output_base}_{feature_id}_"

        if zero_padding is None:
            zero_padding = self.get_max_number_digits(feature_id)

        for value in uniq_values:
            fm = self.get_motl_subset(self, value, feature_id=feature_id, reset_index=True)
            feature_str = str(value).zfill(zero_padding)

            output_txt = f"{outpath}{feature_str}_model.txt"
            output_mod = f"{outpath}{feature_str}.mod"

            fm.scale_coordinates(binning)
            coord_df = pd.DataFrame(fm.get_coordinates(), columns=["x", "y", "z"])
            class_v = fm.df.loc[:, "class"].astype(
                int
            )  # TODO add possibility to create object based on other feature_id
            dummy = pd.Series(np.repeat(1, len(fm)))

            pos_df = pd.concat([class_v, dummy, coord_df], axis=1)
            # pos_df = pos_df.astype(float)
            pos_df.to_csv(output_txt, sep="\t", header=False, index=False)

            # Create model files from the coordinates
            # system(['point2model -sc -sphere ' num2str(point_size) ' ' output_txt ' ' output_mod]);
            subprocess.run(
                [
                    "point2model",
                    "-sc",
                    "-sphere",
                    str(point_size),
                    output_txt,
                    output_mod,
                ]
            )

    def update_coordinates(self):
        """Aplies the existing shifts to x, y, z positions, rounds the new coordinates and stores them as integer
        positions in x, y, z and stores the rest into shifts. After the positions are updated, new extraction of
        subtomograms is necessery.


        Notes
        -----
        The rounding follows round-half-up convention, not the banker's rounding which is default in Python.

        This method modifies the `df` attribute of the object.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """

        # Python 0.5 rounding: round(1.5) = 2, BUT round(2.5) = 2, while in Matlab round(2.5) = 3
        def round_and_recenter(row):
            new_row = row.copy()
            shifted_x = row["x"] + row["shift_x"]
            shifted_y = row["y"] + row["shift_y"]
            shifted_z = row["z"] + row["shift_z"]
            new_row["x"] = float(decimal.Decimal(shifted_x).to_integral_value(rounding=decimal.ROUND_HALF_UP))
            new_row["y"] = float(decimal.Decimal(shifted_y).to_integral_value(rounding=decimal.ROUND_HALF_UP))
            new_row["z"] = float(decimal.Decimal(shifted_z).to_integral_value(rounding=decimal.ROUND_HALF_UP))
            new_row["shift_x"] = shifted_x - new_row["x"]
            new_row["shift_y"] = shifted_y - new_row["y"]
            new_row["shift_z"] = shifted_z - new_row["z"]
            return new_row

        self.df.apply(round_and_recenter, axis=1)
        warnings.warn("The coordinates for subtomogram extraction were changed, new extraction is necessary!")

    def remove_out_of_bounds_particles(self, dimensions, boundary_type="center", box_size=None):
        """Removes particles that are out of tomogram bounds.

        Parameters
        ----------
        dimensions : str
            Filepath or ndarray specifying tomograms' dimensions.
        boundary_type : str, {"center", "whole"}
            Specify whether only the center should be part of the tomogram ("center") or the whole
            box ("whole"). In the latter case, the box_size have to be specified as well. Defaults to "center".
        box_size : int, optional
            Size of the box/subtomogram. It has to be specified if boundary_type is "whole". Defaults to None.

        Notes
        -----
        This method modifies the `df` attribute of the object.

        Returns
        -------
        None

        Raises
        ------
        UserInputError
            In case the boundary_type is "whole" and the box_size is not specified.
        UserInputError
            In case boundary_type is neither "whole" or "center".

        """
        dim = geom.load_dimensions(dimensions)
        original_size = len(self.df)

        # Get type of bounds
        if boundary_type == "whole":
            if box_size:
                boundary = ceil(box_size / 2)
            else:
                raise UserInputError("You need to specify box_size when boundary_type is set to 'whole'.")
        elif boundary_type == "center":
            boundary = 0
        else:
            raise UserInputError(f"Unknown type of boundaries: {boundary_type}")

        recentered = self.get_coordinates()
        idx_list = []
        for i, row in recentered.iterrows():
            tn = row["tomo_id"]
            tomo_dim = dim.loc[dim["tomo_id"] == tn, "x":"z"].reset_index(drop=True)
            c_min = [c - boundary for c in row["x":"z"]]
            c_max = [c + boundary for c in row["x":"z"]]
            if (
                (all(c_min) >= 0)
                and (c_max[0] < tomo_dim["x"][0])
                and (c_max[1] < tomo_dim["y"][0])
                and (c_max[2] < tomo_dim["z"][0])
            ):
                idx_list.append(i)

        self.df = self.df.iloc[idx_list].reset_index(drop=True)

        print(f"Removed {original_size - len(self.df)} particles.")
        print(f"Original size {original_size}, new_size {len(self.df)}")

    @staticmethod
    def recenter_to_subparticle(input_motl, input_mask, rotation=None):
        """Computes the center of mass of the provided binary mask and computes the necessary shift between the mask box
        center and the center of mass. This shift is applied to the motl positions. If rotation is specified it applies
        it to the shifted particles as well.

        Parameters
        ----------
        input_motl: Motl or str or Pandas.DataFrame
            Input motl to apply the recentering to (see :meth:`cryocat.cryomotl.Motl.load` for more details on format)
        input_mask : str
            Binary mask specified either as a file path or ndarray. The box size of the mask
            should correspond to the box size of the reference on which the mask was placed.
        rotation : scipy.spatial.transform._rotation.Rotation
            Rotation to apply on the new positions. Defaults to None.

        Notes
        -----
        This method modifies the `df` attribute of the object.


        Returns
        -------
        :class:`Motl`
            Motl with shifted coordinates.

        """

        if isinstance(input_motl, Motl):
            motl = Motl
        else:
            motl = Motl.load(input_motl)

        input_mask = cryomap.read(input_mask)
        old_center = np.array(input_mask.shape) / 2
        mask_center = cryomask.get_mass_center(input_mask)  # find center of mask
        shifts = mask_center - old_center  # get shifts

        # change shifts in the motl accordingly
        motl.shift_positions(shifts)
        motl.update_coordinates()

        if rotation is not None:
            motl.apply_rotation(rotation)

        return motl

    def shift_positions(self, shift):
        """Shifts the coordinates by the provided shift.

        Parameters
        ----------
        shift : numpy.ndarray
            3D shift to be applied to the coordinates.

        Notes
        -----
        This method modifies the `df` attribute of the object.

        TODO: Check if the row-wise approach cannot be replaced.

        Returns
        -------
        None

        """
        # Shifts positions of all subtomgorams in the motl in the direction given by subtomos' rotations
        # Input: shift - 3D vector - e.g. [1, 1, 1]. A vector in 3D is then rotated around the origin = [0 0 0].
        #               Note that the coordinates are with respect to the origin!

        def shift_coords(row):
            v = np.array(shift)
            euler_angles = np.array([[row["phi"], row["theta"], row["psi"]]])
            orientations = rot.from_euler(seq="zxz", angles=euler_angles, degrees=True)
            rshifts = orientations.apply(v)

            row["shift_x"] = row["shift_x"] + rshifts[0][0]
            row["shift_y"] = row["shift_y"] + rshifts[0][1]
            row["shift_z"] = row["shift_z"] + rshifts[0][2]
            return row

        self.df = self.df.apply(shift_coords, axis=1).reset_index(drop=True)

    def split_in_asymetric_subunits(self, symmetry, xyz_shift):
        """Split the motive list into assymetric subunits.

        Parameters
        ----------
        symmetry : str or number
            Symmetry to be used. Currently cyclic and dihedral symmetry are supported. Cx or
            cx specify the cyclic symmetry of order x, Dx or dx dihedral symmetry of order x. If symmetry is specified
            as int/float, cyclic symmetry is assumed.
        xyz_shift : numpy.ndarray
            Shift by which the center of current particles should be shifted to be centered at first
            subunit.

        Returns
        -------
        :class:`Motl`
            Splitted particle list.

        Warnings
        --------
        This method does not preserve a child class - it always returns :class:`Motl`.

        """
        if isinstance(symmetry, str):
            nfold = int(re.findall(r"\d+", symmetry)[-1])
            if symmetry.lower().startswith("c"):
                s_type = 1  # c symmetry
            elif symmetry.lower().startswith("d"):
                s_type = 2  # d symmetry
            else:
                ValueError("Unknown symmetry - currently only c and are supported!")
        elif isinstance(symmetry, (int, float)):
            s_type = 1  # c symmetry
            nfold = symmetry
        else:
            ValueError(
                "The symmetry has to be specified as a string (starting with c or d) or as a number (float, int)!"
            )

        inplane_step = 360 / nfold

        if s_type == 1:
            n_subunits = nfold
            phi_angles = np.arange(0, 360, int(inplane_step))
            new_angles = np.zeros((n_subunits, 3))
            new_angles[:, 0] = phi_angles
        elif s_type == 2:
            n_subunits = nfold * 2
            in_plane_offset = int(inplane_step / 2)
            new_angles = np.zeros((n_subunits, 3))
            new_angles[0::2, 0] = np.arange(0, 360, int(inplane_step))
            new_angles[1::2, 0] = np.arange(0 + in_plane_offset, 360 + in_plane_offset, int(inplane_step))
            new_angles[1::2, 1] = 180

            phi_angles = new_angles[:, 0].copy()

        phi_angles = phi_angles.reshape(
            n_subunits,
        )

        # make up vectors
        starting_vector = np.array(xyz_shift)
        rho = np.sqrt(starting_vector[0] ** 2 + starting_vector[1] ** 2)
        the = np.arctan2(starting_vector[1], starting_vector[0])

        rot_rho = np.full((n_subunits,), rho)
        rep_the = np.full((n_subunits,), the) + np.deg2rad(phi_angles)
        rep_z = np.full((n_subunits,), starting_vector[2])

        if s_type == 2:
            rep_z[1::2] *= -1

        # https://stackoverflow.com/questions/20924085/python-conversion-between-coordinates
        # [center_shift(:, 1), center_shift(:, 2), center_shift(:, 3)] = pol2cart([0;0.785398163397448;1.570796326794897;2.356194490192345;3.141592653589793;3.926990816987241;4.712388980384690;5.497787143782138], repmat(10,8,1), repmat(0,8,1));
        center_shift = np.zeros([rot_rho.shape[0], 3])
        center_shift[:, 0] = rot_rho * np.cos(rep_the)
        center_shift[:, 1] = rot_rho * np.sin(rep_the)
        center_shift[:, 2] = rep_z

        new_motl_df = pd.concat([self.df] * n_subunits)

        new_motl_df["geom5"] = new_motl_df["subtomo_id"]
        new_motl_df = new_motl_df.sort_values(by="subtomo_id")
        new_motl_df["geom2"] = np.tile(np.arange(1, n_subunits + 1).reshape(n_subunits, 1), (len(self.df), 1))

        euler_angles = new_motl_df[["phi", "theta", "psi"]]
        rotations = rot.from_euler(seq="zxz", angles=euler_angles, degrees=True)
        center_shift = np.tile(center_shift, (len(self.df), 1))
        new_angles = np.tile(new_angles, (len(self.df), 1))
        new_motl_df.loc[:, ["shift_x", "shift_y", "shift_z"]] = new_motl_df.loc[
            :, ["shift_x", "shift_y", "shift_z"]
        ] + rotations.apply(center_shift)

        new_rotations = rotations * rot.from_euler(seq="zxz", angles=new_angles, degrees=True)
        new_motl_df.loc[:, ["phi", "theta", "psi"]] = new_rotations.as_euler(seq="zxz", degrees=True)

        new_motl_df["subtomo_id"] = np.arange(1, len(new_motl_df) + 1)
        new_motl = Motl(new_motl_df)
        new_motl = new_motl.update_coordinates()
        new_motl.df.reset_index(inplace=True, drop=True)
        return new_motl


class EmMotl(Motl):
    def __init__(self, input_motl=None, header=None):
        if input_motl is not None:
            if isinstance(input_motl, pd.DataFrame):
                self.check_df_type(input_motl)
            elif isinstance(input_motl, str):
                self.df, self.header = self.read_in(input_motl)
            else:
                raise UserInputError(
                    f"Provided input_motl is neither DataFrame nor path to the motl file: {input_motl}."
                )
        else:
            self.df = Motl.create_empty_motl_df()

        self.header = header if header else {}

    def convert_to_motl(self, input_df):
        """Convert input_df to correct motl_df format. In this class, it is expected to have the correct format already
        and no conversion is provided. If this function is called the format of the input is incorrect and error is
        raised.

        Parameters
        ----------
        input_df : pandas.DataFrame

        Returns
        -------
        None

        Raises
        ------
        UserInputError
            The provided input_df should be in correct format

        """

        raise ValueError("Provided motl does not have the correct format.")

    @staticmethod
    def read_in(emfile_path):
        """Reads in an EM file and returns a pandas DataFrame and header.

        Parameters
        ----------
        emfile_path : str
            The path to the EM file.

        Returns
        -------
        motl_df : pandas.DataFrame
           Particle list.
        header : dict
            Header from the the parsed EM file.

        Raises
        ------
        UserInputError
            If the provided file does not exist or if it contains a different number of columns than expected.

        """

        if not os.path.isfile(emfile_path):
            raise UserInputError(f"Provided file {emfile_path} does not exist.")

        header, parsed_emfile = emfile.read(emfile_path)
        if not len(parsed_emfile[0][0]) == 20:
            raise UserInputError(
                f"Provided file contains {len(parsed_emfile[0][0])} columns, while 20 columns are expected."
            )

        motl_df = pd.DataFrame(data=parsed_emfile[0], dtype=float, columns=Motl.motl_columns)

        return motl_df, header

    def write_out(self, output_path):
        """Writes out the dataframe as emfile.

        Parameters
        ----------
        output_path : str
            Name of the file to be written out (including the path).

        Returns
        -------
        None

        """
        filled_df = self.df.fillna(0.0)
        motl_array = filled_df.to_numpy()
        motl_array = motl_array.reshape((1, motl_array.shape[0], motl_array.shape[1])).astype(np.single)
        self.header = {}  # FIXME fails on writing back the header
        emfile.write(output_path, motl_array, self.header, overwrite=True)


class RelionMotl(Motl):
    default_version = 3.1
    columns_v3_0 = [
        "rlnMicrographName",
        "rlnCoordinateX",
        "rlnCoordinateY",
        "rlnCoordinateZ",
        "rlnAngleRot",
        "rlnAngleTilt",
        "rlnAnglePsi",
        "rlnImageName",
        "rlnPixelSize",
        "rlnRandomSubset",
        "rlnOriginX",
        "rlnOriginY",
        "rlnOriginZ",
        "rlnClassNumber",
    ]

    columns_v3_1 = [
        "rlnMicrographName",
        "rlnCoordinateX",
        "rlnCoordinateY",
        "rlnCoordinateZ",
        "rlnAngleRot",
        "rlnAngleTilt",
        "rlnAnglePsi",
        "rlnImageName",
        "rlnPixelSize",
        "rlnOpticsGroup",
        "rlnGroupNumber",
        "rlnOriginXAngst",
        "rlnOriginYAngst",
        "rlnOriginZAngst",
        "rlnClassNumber",
        "rlnRandomSubset",
    ]

    columns_v4 = [
        "rlnCoordinateX",
        "rlnCoordinateY",
        "rlnCoordinateZ",
        "rlnAngleRot",
        "rlnAngleTilt",
        "rlnAnglePsi",
        "rlnTomoName",
        "rlnTomoParticleName",
        "rlnRandomSubset",
        "rlnOpticsGroup",
        "rlnOriginXAngst",
        "rlnOriginYAngst",
        "rlnOriginZAngst",
        "rlnGroupNumber",
        "rlnClassNumber",
    ]

    def __init__(self, input_motl=None, version=None, pixel_size=None, binning=None, optics_data=None):
        super().__init__()
        self.version = version
        self.pixel_size = pixel_size
        self.binning = binning
        self.relion_df = pd.DataFrame()
        self.optics_data = optics_data
        self.tomo_id_name = ""
        self.subtomo_id_name = ""
        self.shifts_id_names = []
        self.data_spec = ""

        if input_motl is not None:
            if isinstance(input_motl, pd.DataFrame):
                self.check_df_type(input_motl)
            elif isinstance(input_motl, str):
                relion_df, data_version, optics_df = self.read_in(input_motl)
                self.convert_to_motl(relion_df, data_version, optics_df)
            else:
                raise UserInputError(
                    f"Provided input_motl is neither DataFrame nor path to the motl file: {input_motl}."
                )
        else:
            self.set_version(self.relion_df, version)
            self.set_pixel_size()

        self.set_version_specific_names()

    def set_pixel_size(self):
        """Sets the pixel size of the object (self.pixel_size). The function first checks if the pixel size has already
        been set, and if it has not, then it will try to get the pixel size from either the self.relion_df or
        self.optics_data dataframes. If neither of these are available, then it is set to 1.0.

        Notes
        -----
        Pixel size is important to correctly compute shifts for Relion version > 3.1 and also for correctly
        rescaling cooridantes for Relion version > 4.0.

        Parameters
        ----------
        None

        Returns
        -------
        None

        """

        # pixel size is already set, do not try to get it from the data
        if self.pixel_size is not None:
            return

        if "rlnPixelSize" in self.relion_df.columns:
            self.pixel_size = self.relion_df["rlnPixelSize"].values
        elif self.optics_data is not None:
            pixel_size_optics = []
            optic_groups = []
            if "rlnImagePixelSize" in self.optics_data.columns:
                pixel_size_optics.append(self.optics_data["rlnImagePixelSize"].values)
                optic_groups.append(self.optics_data["rlnOpticsGroup"].values)
            if len(self.optics_data) == 1:
                self.pixel_size = pixel_size_optics[0]
            else:
                # TODO: test this
                self.pixel_size = np.zeros((self.relion_df.shape[0],))
                for ps, og in zip(pixel_size_optics, optic_groups):
                    self.pixel_size[self.relion_df["rlnOpticsGroup"] == og] = ps
        else:
            self.pixel_size = 1.0
            warnings.warn("Could not determine the pixel size from the data. The pixel size is set to 1.0.")

    @staticmethod
    def get_version_from_file(frames, specifiers):
        """Determines the version of Relion that was used to generate a starfile.

        Parameters
        ----------
        frames : list
            List of DataFrames loaded from a starfile. The length corresponds to the length of the specifiers list.
        specifiers : list
            List of data specifiers (`str` type) loaded from a starfile. The length corresponds to the length
            of the frames list.

        Returns
        -------
        float
            A version number.


        """
        version = None
        for s in specifiers:
            if "data_" == s:
                version = 3.0
            elif "data_particles" == s:
                frame_index = specifiers.index("data_particles")
                if "rlnTomoName" in frames[frame_index].columns or "rlnTomoParticleName" in frames[frame_index].columns:
                    version = 4.0
                else:
                    version = 3.1

        return version

    def set_version(self, input_df, version=None):
        """Sets the class attribute version, in case it has not been set already. The function takes in a
        pandas.DataFrame and an optional version number as arguments. If no version number is provided, the function
        will attempt to determine which Relion version was used by checking for specific column names in the DataFrame.
        If it cannot find any of these columns, it will default to version 3.1.

        Parameters
        ----------
        input_df : pandas.DataFrame
            DataFrame in Relion format that is used for version determination, unless the class attribute was already
            set or version argument is not none.
        version : float, optional
            Set the version of the data unless it was already set. Defaults to None.

        Returns
        -------
        None

        """

        # pixel size is already set, do not try to get it from the data
        if self.version is not None:
            return

        if version is not None:
            self.version = version
        elif "rlnTomoName" in input_df.columns or "rlnTomoParticleName" in input_df.columns:
            self.version = 4.0
        elif "rlnMicrographName" in input_df.columns and "rlnOriginXAngst" in input_df.columns:
            self.version = 3.1
        elif "rlnMicrographName" in input_df.columns and "rlnOriginX" in input_df.columns:
            self.version = 3.0
        else:
            self.version = 3.1
            warnings.warn("Could not determine the version from the data. The version is set to 3.1.")

    @staticmethod
    def _get_data_particles_id(input_list):
        """Find the index of the first occurrence of either "data_particles" or "data_" in the given input list. For
        Relion version 3.1 and higher, the particle list is specified by "data_particles", while for lower versions
        the specifier is "data_".

        Parameters
        ----------
        input_list : list
            The list to search for the desired strings.

        Returns
        -------
        int
            The index of the first occurrence of either "data_particles" or "data_" in the input list.

        Raises
        ------
        UserInputError
            If neither "data_particles" nor "data_" is found in the input list.


        """

        if "data_particles" in input_list:
            return input_list.index("data_particles")
        elif "data_" in input_list:
            return input_list.index("data_")
        else:
            raise UserInputError("The starfile does not contain particle list.")

    @staticmethod
    def _get_optics_id(input_list):
        """Returns the index of the element "data_optics" in the input list. The specifier "data_optics" is used only
        from Relion version 3.1 and higher. The lower version have data optics specified as part of the particle list.

        Parameters
        ----------
        input_list : list
            The list to search for the element.

        Returns
        -------
        int or None
            The index of the element 'data_optics' if found, otherwise None.

        Notes
        -----
        Currently returns only optics data for Relion version 3.1 and higher.

        TODO: Add support for Relion 3.0 and lower.

        """

        if "data_optics" in input_list:
            return input_list.index("data_optics")
        else:
            return None

    def read_in(self, input_path):
        """Reads in a starfile and returns the particle list, version of the starfile and optics data if present.

        Parameters
        ----------
        input_path : str
            The path to the starfile.

        Returns
        -------
        frames : pandas.DataFrame
            Pandas.DataFrame containing the particle list in relion format.
        version : float
            The version extracted from the starfile. See meth:`cryocat.cryomotl.RelionMotl.get_version_from_file` for
            more info.
        optics_df : pandas.DataFrame or None
            Pandas.DataFrame containing optics if available, otherwise None.

        """

        frames, specifiers, _ = starfileio.Starfile.read(input_path)

        version = RelionMotl.get_version_from_file(frames, specifiers)

        data_id = RelionMotl._get_data_particles_id(specifiers)
        optics_id = RelionMotl._get_optics_id(specifiers)

        optics_df = None
        if optics_id is not None and self.optics_data is None:
            optics_df = frames[optics_id]

        return frames[data_id], version, optics_df

    def convert_angles_from_relion(self, relion_df):
        """The function converts angles from the Relion format, which corresponds to ZYZ Euler convention,
        to the zxz Euler convention which is used within cryoCAT.

        Parameters
        ----------
        relion_df : pandas.DataFrame
            The input DataFrame ir Relion format containing the angles in ZYZ convention.

        Returns
        -------
        None

        Raises
        ------
        Warning
            If no rotations are specified in the relion starfile.
        ValueError
            If only some rotations are specified in the relion starfile.

        Notes
        -----
        The function modifies the "phi", "psi", and "theta" columns of "self.df" to store the converted angles.

        """

        # angles list
        relion_angles = ["rlnAngleRot", "rlnAngleTilt", "rlnAnglePsi"]
        # check the entries:
        columns_exist = [item in relion_df.columns for item in relion_angles]

        if all(columns_exist):
            pass
        elif not any(columns_exist):
            warnings.warn("Rotations are not specified in the relion starfile!")
        else:
            raise ValueError("Only some rotations are specified in the relion starfile!")

        # getting the angles
        angles = relion_df.loc[:, relion_angles].to_numpy()

        # convert from ZYZ to zxz
        rot_ZYZ = rot.from_euler("ZYZ", angles, degrees=True)
        rot_zxz = rot_ZYZ.as_euler("zxz", degrees=True)

        # save so the rotation describes reference rotation
        self.df["phi"] = -rot_zxz[:, 2]
        self.df["theta"] = -rot_zxz[:, 1]
        self.df["psi"] = -rot_zxz[:, 0]

    def convert_angles_to_relion(self, relion_df):
        """Converts angles from cryoCAT convention (zxz) to the convention used in Relion (ZYZ).

        Parameters
        ----------
        relion_df : pandas.DataFrame
            The DataFrame containing the angles.

        Returns
        -------
        pandas.DataFrame
            DataFrame in Relion format with converted angles.

        Notes
        -----
        TODO: Check why ZXZ is used instead of zxz.

        """

        rotations = rot.from_euler("ZXZ", self.get_angles(), degrees=True)
        angles = rotations.as_euler("ZYZ", degrees=True)

        # save so the rotation describes reference rotation
        relion_df["rlnAngleRot"] = -angles[:, 0]
        relion_df["rlnAngleTilt"] = angles[:, 1]
        relion_df["rlnAnglePsi"] = -angles[:, 2]

        return relion_df

    def convert_shifts(self, relion_df):
        """Converts shifts from Relion format to emmotl format and stores them in self.df.

        Parameters
        ----------
        relion_df : pandas.DataFrame
            DataFrame containing shifts in Relion format.

        Warnings
        --------
        Shifts in Relion 3.1 and higher are stored in Angstroms, not pixels/voxels. Correct pixel size is thus
        necessary for correct conversion. The pixel size should be set as the class attribute before calling this
        function.

        Notes
        -----
        Relion stores the shifts of the particle while in cryoCAT the shifts represent shifts of a reference.

        Returns
        -------
        None

        """

        for motl_column, rln_column in zip(("shift_x", "shift_y", "shift_z"), self.shifts_id_names):
            self.assign_column(relion_df, {motl_column: rln_column})

            # conversions of shifts - emmotl stores shifts for the reference, relion for the subtomo
            self.df[motl_column] = -self.df[motl_column].values

            if self.version >= 3.1:
                self.df[motl_column] = self.df[motl_column].values / self.pixel_size

    def parse_tomo_id(self, relion_df):
        """The function parses the tomogram id from a Relion starfile. The function takes
        in a pandas.DataFrame in relion format and looks for the `rlnMicrographName` (for Relion 3.1 and lower) column
        or for the `rlnTomoName` (for Relion 4.0 and higher) column and tries to parse the tomogram id for each
        particle. If the column is not present it tries to parse the tomo id from the subtomogram path (`rlnImageName`
        for relion 3.1 and lower, `rlnTomoName` for Relion 4.0 and higher).

        Parameters
        ----------
        relion_df : pandas.DataFrame
            The DataFrame in Relion format containing the tilt-series or micrographs.

        Warnings
        --------
        Due to lack of format in relion starfiles it is possible that this function will fail. Currently, following
        formats are expected:

        - Relion 3.1 and lower for "rlnMicrographName": first number in the last entry (/path/tomoID_pixelSize.mrc)
        - Relion 3.1 and lower for "rlnImageName": first number in the last entry (/path/tomoID_subtomoID_pixelSize.mrc)
        - Relion 4.0 and higher for "rlnTomoName": first number in the last entry (TS_tomoID)
        - Relion 4.0 and higher for "rlnTomoParticleName": first number in the first entry (TS_tomoID/subtomoID)

        Notes
        -----
        TODO: Add custom format specifier.

        """

        if self.tomo_id_name in relion_df.columns:
            micrograph_names = relion_df[self.tomo_id_name].tolist()

            if all(isinstance(i, (int, float)) for i in micrograph_names):
                tomo_idx = micrograph_names
            else:
                tomo_names = [i.rsplit("/", 1)[-1] for i in micrograph_names]
                tomo_idx = []

                for j in tomo_names:
                    tomo_idx.append(float(re.search(r"\d+", j).group()))

            self.df["tomo_id"] = tomo_idx

        # in case there is no migrograph name fetch tomo id from subtomo path
        elif self.subtomo_id_name in relion_df.columns:
            if self.version <= 3.1:
                tomo_position = -1
            else:
                tomo_position = 0
            micrograph_names = relion_df[self.subtomo_id_name].tolist()

            if all(isinstance(i, (int, float)) for i in micrograph_names):
                tomo_idx = micrograph_names
            else:
                tomo_names = [i.rsplit("/", 1)[tomo_position] for i in micrograph_names]
                tomo_idx = []

                for j in tomo_names:
                    tomo_idx.append(float(re.findall(r"\d+", j)[0]))

            self.df["tomo_id"] = tomo_idx

    def parse_subtomo_id(self, relion_df):
        """The function parses the subtomogram id from a Relion starfile. The function takes
        in a pandas.DataFrame in relion format and looks for the `rlnImageName` (for Relion 3.1 and lower) column
        or for the `rlnTomoParticleName` (for Relion 4.0 and higher) column and tries to parse the subtomogram id for each
        particle. It checks whether the subtomogram indices are unique and if not, it renumbers the `subtomo_id` to a
        sequence from 1 to length of the particle list and stores the original value in `geom3`.

        Parameters
        ----------
        relion_df : pandas.DataFrame
            The DataFrame in Relion format containing the subtomogram numbers.

        Notes
        -----
        The function modifies the `subtomo_id` column of `self.df` to store the subtomogram indices. In case they are
        not uniqe it also modifies `geom3` columns of `self.df`.

        TODO: Add custom format specifier.

        Warnings
        --------
        Due to lack of format in relion starfiles it is possible that this function will fail. Currently, following
        formats are expected:

        - Relion 3.1 and lower for "rlnImageName": second number in the last entry (/path/tomoID_subtomoID_pixelSize.mrc)
        - Relion 4.0 and higher for "rlnTomoParticleName": the only number in the last entry (TS_tomoID/subtomoID)
        - Relion 4.0 and higher for "rlnTomoParticleName": the only number in the last entry (TS_tomoID/subtomoID)

        Returns
        -------
        None

        """
        # parsing out subtomo number
        if self.subtomo_id_name in relion_df.columns:
            image_names = relion_df[self.subtomo_id_name].tolist()

            # Note: following will fail if the subtomos are named differently for each row - once with string, once with
            # number
            if all(isinstance(i, (int, float)) for i in image_names):
                subtomo_idx = image_names
            else:
                subtomo_names = [i.rsplit("/", 1)[-1] for i in image_names]
                subtomo_idx = []

                for j in subtomo_names:
                    if self.version >= 4.0:
                        subtomo_idx.append(float(j))
                    else:
                        subtomo_idx.append(float(re.findall(r"\d+", j)[1]))

        # Check if the subtomo_idx are unique and if not store them at geom3 and renumber particles
        self.df["geom3"] = subtomo_idx
        self.df["subtomo_id"] = subtomo_idx

        if len(np.unique(subtomo_idx)) != len(subtomo_idx):
            self.df["subtomo_id"] = np.arange(1, relion_df.shape[0] + 1, 1)

        # If there is information about half-sets renumber the subtomo_idx accordintly
        if "rlnRandomSubset" in relion_df.columns and relion_df["rlnRandomSubset"].nunique() == 2:
            halfset_num = relion_df["rlnRandomSubset"].values % 2
            subtomo_id_num = []
            c = 0 if halfset_num[0] == 1 else 2
            for i in range(0, self.df.shape[0]):
                c = np.ceil(c / 2) * 2 + halfset_num[i]
                subtomo_id_num.append(c)

            self.df["subtomo_id"] = subtomo_id_num

    def convert_to_motl(self, relion_df, version=None, optics_df=None):
        """The function converts a DataFrame in relion format into a motl DataFrame.

        Parameters
        ----------
        relion_df : pandas.DataFrame
            DataFrame in relion format.
        version : float, optional
            Version of Relion DataFrame. Defaults to None.
        optics_df : pandas.DataFrame, optional
            DataFrame with optics data. Defaults to None

        Notes
        -----
        This method modifies the `df` attribute of the object.

        Returns
        -------
        None

        """

        if self.optics_data is None and isinstance(optics_df, pd.DataFrame):
            self.optics_data = optics_df

        self.relion_df = relion_df.copy()
        self.relion_df.reset_index(inplace=True, drop=True)

        self.set_version(relion_df, version)
        self.set_pixel_size()
        self.set_version_specific_names()

        # assign coordinates
        for coord in ("x", "y", "z"):
            relion_column = "rlnCoordinate" + coord.upper()
            self.assign_column(relion_df, {coord: relion_column})

        self.convert_shifts(relion_df)
        self.convert_angles_from_relion(relion_df)

        self.parse_tomo_id(relion_df)
        self.parse_subtomo_id(relion_df)

        self.assign_column(relion_df, {"class": "rlnClassNumber"})  # getting class number
        self.assign_column(
            relion_df, {"score": "rlnMaxValueProbDistribution"}
        )  # getting the max value contribution per distribution - not really same as CCC but has similar indications

        # store the idx of the original data - useful for writing out
        self.relion_df["ccSubtomoID"] = self.df["subtomo_id"]

    def adapt_original_entries(self):
        """The function updates DataFrame stored in `self.relion_df` based on the values in `self.df`.
        In case the number of particles changed (i.e., `self.df` has less particles than `self.relion_df`), the new
        relion_df is shortened based on `ccSubtomoID` from self.relion_df and `subtomo_id` from `self.df`. The shifts
        are set to zeros and `ccSubtomoID` is removed.

        Parameters
        ----------
        None

        Returns
        -------
        pandas.DataFrame
            The updated version of `self.relion_df`.

        Notes
        -----
        The size and values of `self.relion_df` are not changed.

        """

        if self.relion_df.empty:
            raise UserInputError(f"There are no original entries for this relion motl, set original_entries to False.")

        original_data = self.relion_df
        # In case some particles were removed unify the frames
        original_data = original_data[original_data["ccSubtomoID"].isin(self.df["subtomo_id"])].reset_index(drop=True)

        # Change order of the rows in the original data to correspond to the new motl
        original_data = original_data.set_index("ccSubtomoID")
        original_data = original_data.reindex(index=self.df["subtomo_id"])
        original_data = original_data.reset_index()
        # original_data = original_data.drop("ccSubtomoID", axis=1)

        if "rlnOriginXAngst" in original_data.columns:
            original_data.loc[:, ["rlnOriginXAngst", "rlnOriginYAngst", "rlnOriginZAngst"]] = np.zeros(
                (original_data.shape[0], 3)
            )
        else:
            original_data.loc[:, ["rlnOriginX", "rlnOriginY", "rlnOriginZ"]] = np.zeros((original_data.shape[0], 3))

        return original_data

    def set_version_specific_names(self):
        """Sets version specific names for the current object.

        Notes
        -----
        This function sets the following attributes of the current object:
        - "tomo_id_name": The name of the tomogram ID ("rlnMicrographName" for Relion 3.1 and lower, "rlnTomoName" for
        Relion 4.0 and higher).
        - "subtomo_id_name": The name of the subtomogram ID ("rlnImageName" for Relion 3.1 and lower,
        "rlnTomoParticleName" for Relion 4.0 and higher).
        - "shifts_id_names": The names of the shift IDs ("rlnOriginX" for Relion 3.0 and lower, "rlnOriginXAngst" for
        Relion 3.1 and higher).
        - "data_spec": The particle list specification ("data" for Relion 3.0 and lower, "data_particles" for Relion 3.1 and higher).

        Parameters
        ----------
        None

        Returns
        -------
        None

        """

        (
            self.tomo_id_name,
            self.subtomo_id_name,
            self.shifts_id_names,
            self.data_spec,
        ) = RelionMotl.get_version_specific_names(self.version)

    @staticmethod
    def get_version_specific_names(version):
        """The function returns the version-specific names of columns in Relion DataFrame.

        Parameters
        ----------
        version : float
            The version number.

        Returns
        -------
        tomo_id_name : str
            The name for the tomogram ID ("rlnMicrographName" for Relion 3.1 and lower, "rlnTomoName" for Relion 4.0 and higher).
        subtomo_id_name : str
            The name for the subtomogram ID ("rlnImageName" for Relion 3.1 and lower, "rlnTomoParticleName" for Relion 4.0 and higher).
        shifts_id_names : list
            A list of names (type `str`) for the shift coordinates("rlnOriginX" for Relion 3.0 and lower, "rlnOriginXAngst"
            for Relion 3.1 and higher).
        data_spec : str
            The name for the particle list specification ("data" for Relion 3.0 and lower, "data_particles" for Relion 3.1 and higher).

        """
        if version is None:
            version = RelionMotl.default_version

        if version <= 3.0:
            tomo_id_name = "rlnMicrographName"
            subtomo_id_name = "rlnImageName"
            shifts_id_names = ["rlnOriginX", "rlnOriginY", "rlnOriginZ"]
            data_spec = "data_"
        elif version == 3.1:
            tomo_id_name = "rlnMicrographName"
            subtomo_id_name = "rlnImageName"
            shifts_id_names = ["rlnOriginXAngst", "rlnOriginYAngst", "rlnOriginZAngst"]
            data_spec = "data_particles"
        else:
            tomo_id_name = "rlnTomoName"
            subtomo_id_name = "rlnTomoParticleName"
            shifts_id_names = ["rlnOriginXAngst", "rlnOriginYAngst", "rlnOriginZAngst"]
            data_spec = "data_particles"

        return tomo_id_name, subtomo_id_name, shifts_id_names, data_spec

    def create_particles_data(self, version):
        """Creates an empty DataFrame in Relion version-specific format with the size corresponding to `self.df`.

        Parameters
        ----------
        version : float
            The version of the Relion to be used. Valid values are 3.0, 3.1, and any other value for version
            4 or higher.

        Returns
        -------
        pandas.DataFrame
            The empty DataFrame with columns corresponding to the specified Relion version.

        """

        if version == 3.0:
            relion_df = pd.DataFrame(
                data=np.zeros((self.df.shape[0], len(self.columns_v3_0))), columns=self.columns_v3_0
            )
        elif version == 3.1:
            relion_df = pd.DataFrame(
                data=np.zeros((self.df.shape[0], len(self.columns_v3_1))), columns=self.columns_v3_1
            )
        else:
            relion_df = pd.DataFrame(data=np.zeros((self.df.shape[0], len(self.columns_v4))), columns=self.columns_v4)

        return relion_df

    def prepare_optics_data(self, use_original_entries=True, optics_data=None, version=None):
        """The function prepares the optics data for relion DataFrame. It takes in a dictionary or starfile path as an
        argument, and returns a pandas DataFrame containing the optics information in version specific format.

        Parameters
        ----------
        use_original_entries : bool, default=True
            Whether to use the `self.optics_df` (True) as source or not. If set to True, the optics_data as well as
            version will be ignored. Defaults to True.
        optics_data : str, optional
            The optics data specified either as a path to the starfile (it can also contain the particle list) or as
            DataFrame. It is used only if "use_original_entries" is set to False. Defaults to None.
        version : float, optional
            Relion version to be used for the DataFrame. It is used only if use_original_entries is set to False and
            the "optics_data" is a path to starfile. If not set, `self.version` will be used instead. Defaults to None.

        Returns
        -------
        pandas.DataFrame
            DataFrame with the optics data.

        Raises
        ------
        UserInputError
            If `optics_data` is not str nor pandas.DataFrame.
        Warning
            If `optics_data` is not specified and `self.optics_df` is empty.

        """

        optics_df = []

        if use_original_entries:
            if self.optics_data is not None:
                optics_df = self.optics_data
            else:
                raise Warning(
                    f"There is no information on optics available - use optics_data argument to provide this information."
                )
        elif optics_data is not None:
            if isinstance(optics_data, str):
                if version is None:
                    version = self.version

                if version >= 3.1:
                    optics_df, _ = starfileio.get_frame_and_comments(optics_data, "data_optics")
                else:
                    optics_df, _ = starfileio.get_frame_and_comments(optics_data, "data_")
            elif isinstance(optics_data, dict):
                optics_df = pd.DataFrame(optics_data)
            else:
                raise UserInputError("Optics has to be specified as a dictionary or as a path to the starfile.")
        else:
            raise Warning(
                f"There is no information on optics available - use optics_data argument to provide this information."
            )

        return optics_df

    def prepare_particles_data(self, tomo_format="", subtomo_format="", version=None, pixel_size=None):
        """The function creates a DataFrame that contains the information on particles in Relion format. The function
        takes in the version of Relion to be used and formats describining how the tomogram/tilt-series and subtomogram
        names should be assembled.

        Parameters
        ----------
        tomo_format : str, default=""
            Format specifying the tomogram/tilt-series name by containing sequence of "x"
            introduced by "$" character. The longest sequence is evaluated as the position of the tomo_id and
            replaced with corresponding tomo_id. The number of x letters of the longest sequence determines number
            of digits to pad with zero. For example, for tomo_id 5 will following format "/path/to/tomo/$xxxx.rec"
            result in "/path/to/tomo/0005.rec". The sequence can be present multiple times, sequences of "x" shorter
            than the longest one will be kept intact: for tomo_id 5 will "/path/to/tomo/$xxxx/$xxxx_$xx.mrc
            result in "/path/to/tomo/0005/0005_$xx.mrc". Defaults to empty string, in which case the tomo_id will be
            used without any zero padding.
        subtomo_format : str, default=""
            Format specifying the subtomogram name by containing sequence of "y" introduced by "$" character.
            The longest sequence is evaluated as the position of the subtomo_id and replaced with corresponding
            subtomo_id. The number of "y" letters of the longest sequence determines number of digits to pad with zero.
            For example, for subtomo_id 65 with following format "/path/to/subtomograms/$yyy.mrc" will result
            in /path/to/subtomograms/065.mrc". The sequence can be present multiple times, sequences of "y" shorter
            than the longest one will be kept intact: for subtomo_id 65 will "/path/to/subtomograms/$yy_$yyy.mrc"
            result in "/path/to/subtomograms/$yy_065.mrc". The subtomo_format can also contain sequence of "x" letters
            introduced by "$" in which case these are replaced by tomo_id in the same way as for tomo_format.
            For example, for tomo_id 5 and subtomogram_id 65 the following "/path/to/subtomograms/$xxxx/$xxxx_$yyy.mrc"
            will result in "/path/to/subtomograms/0005/0005_065.mrc". Defaults to empty string, in which case the
            subtomo_id will be used without any zero padding.
        version : float, optional
            Relion version to be used for the DataFrame. Defaults to None, in which case `self.version` is used.
        pixel_size : float, optional
            The pixel size of the data. If not provided, the pixel size of the object instance (`self.pixel_size`) will
            be used. Defaults to None.

        Returns
        -------
        pandas.DataFrame
            A DataFrame with particle list in Relion format.

        Raises
        ------
        UserInputError
            In case the format does not contain valid sequence.

        Examples
        --------

        >>> rln_motl = cryomotl.RelionMotl()
        >>> rln_motl.fill({"tomo_id": [2], "subtomo_id":[65]})

        >>> rln_df = rln_motl.prepare_particles_data(tomo_format="/path/to/$xxxx.rec",
        ... subtomo_format="/path/to/$xxxx/$xxxx_$yy_2.6A.mrc", version=3.1)
        >>> print(rln_df["rlnMicrographName"].values[0])
        >>> print(rln_df["rlnImageName"].values[0])
        /path/to/0002.rec
        /path/to/0002/0002_65_2.6A.mrc

        >>> rln_df = rln_motl.prepare_particles_data(tomo_format="/path/to/$xxxx",
        ... subtomo_format="/path/to/$xxxx/$xxxx_$yy_2.6A", version=4.0)
        >>> print(rln_df["rlnTomoName"].values[0])
        >>> print(rln_df["rlnTomoParticleName"].values[0])
        /path/to/0002
        /path/to/0002/0002_65_2.6A

        >>> rln_df = rln_motl.prepare_particles_data(tomo_format="/path/to/$xx.rec",
        ... subtomo_format="/path/to/xxxx/xxxx_$yy_2.6A.mrc", version=3.1)
        >>> print(rln_df["rlnMicrographName"].values[0])
        >>> print(rln_df["rlnImageName"].values[0])
        /path/to/02.rec
        /path/to/xxxx/xxxx_65_2.6A.mrc

        >>> rln_df = rln_motl.prepare_particles_data(tomo_format="",
        ... subtomo_format="/path/to/$xxx/$yy_2.6A.mrc", version=3.1)
        >>> print(rln_df["rlnMicrographName"].values[0])
        >>> print(rln_df["rlnImageName"].values[0])
        2
        /path/to/002/65_2.6A.mrc

        >>> rln_df = rln_motl.prepare_particles_data(tomo_format="",
        ... subtomo_format="/path/to/$xxx/yy_2.6A.mrc", version=3.1)
        >>> print(rln_df["rlnMicrographName"].values[0])
        >>> print(rln_df["rlnImageName"].values[0])
        ValueError: The format /path/to/$xxx/yy_2.6A.mrc does not contain any sequence of \$ followed by y.
        """

        def find_longest_sequence(test_string, test_letter, raise_error=True):
            pattern = f"\$(?:{test_letter})+"
            findings = sorted(re.findall(pattern, test_string), key=len)
            if not findings:
                if raise_error:
                    raise ValueError(
                        f"The format {test_string} does not contain any sequence of \$ followed by {test_letter}."
                    )
                else:
                    return None, 0
            else:
                longest_sequence = findings[-1]
                return longest_sequence, len(longest_sequence) - 1

        if version is None:
            version = self.version

        if pixel_size is None:
            pixel_size = self.pixel_size

        tomo_name, subtomo_name, shifts_name, _ = RelionMotl.get_version_specific_names(version)
        relion_df = self.create_particles_data(version)

        if tomo_format == "":
            relion_df[tomo_name] = self.df["tomo_id"].astype(int)
        else:
            tomo_sequence, tomo_digits = find_longest_sequence(tomo_format, "x")
            # add temporarily tomo_id
            relion_df["tomo_id"] = self.df["tomo_id"].values

            relion_df[tomo_name] = tomo_format
            relion_df[tomo_name] = relion_df.apply(
                lambda row: row[tomo_name].replace(tomo_sequence, str(int(row["tomo_id"])).zfill(tomo_digits)), axis=1
            )

            # drop the column
            relion_df = relion_df.drop(["tomo_id"], axis=1)

        if subtomo_format == "":
            relion_df[subtomo_name] = self.df["subtomo_id"].values.astype(int)
        else:
            subtomo_sequence, subtomo_digits = find_longest_sequence(subtomo_format, "y")
            subtomo_t_sequence, subtomo_t_digits = find_longest_sequence(subtomo_format, "x", raise_error=False)

            # add temporarily tomo_id and subtomo_id
            relion_df["tomo_id"] = self.df["tomo_id"].values
            relion_df["subtomo_id"] = self.df["subtomo_id"].values

            relion_df[subtomo_name] = subtomo_format
            relion_df[subtomo_name] = relion_df.apply(
                lambda row: row[subtomo_name].replace(
                    subtomo_sequence, str(int(row["subtomo_id"])).zfill(subtomo_digits)
                ),
                axis=1,
            )

            if subtomo_t_sequence is not None:
                relion_df[subtomo_name] = relion_df.apply(
                    lambda row: row[subtomo_name].replace(
                        subtomo_t_sequence, str(int(row["tomo_id"])).zfill(subtomo_t_digits)
                    ),
                    axis=1,
                )

            # drop the columns
            relion_df = relion_df.drop(["tomo_id", "subtomo_id"], axis=1)

        relion_df.loc[:, shifts_name] = np.zeros((relion_df.shape[0], 3))

        if version < 4.0:
            relion_df["rlnPixelSize"] = pixel_size

        return relion_df

    def create_optics_group_v3_1(self, pixel_size=None, subtomo_size=None):
        """Creates an optics group with default parameters corresponding to Relion v. 3.1.

        Parameters
        ----------
        pixel_size : float, optional
            The pixel size of the data. If not provided, the pixel size of the object instance (`self.pixel_size`) will
            be used. Defaults to None.
        subtomo_size : int, optional
            The size of the subtomograms. If not provided, it will be set to "NaN". Defaults to None.

        Returns
        -------
        pandas.DataFrame
            A DataFrame containing the default optics parameters.

        """

        pixel_size = pixel_size if pixel_size is not None else self.pixel_size
        subtomo_size = subtomo_size if subtomo_size is not None else "NaN"

        optics_default = {
            "rlnOpticsGroup": 1,
            "rlnOpticsGroupName": "opticsGroup1",
            "rlnSphericalAberration": 2.7,
            "rlnVoltage": 300.0,
            "rlnImagePixelSize": pixel_size,
            "rlnImageSize": subtomo_size,
            "rlnImageDimensionality": 3,
        }

        if pixel_size is not None:
            optics_default["rlnImagePixelSize"] = pixel_size

        if subtomo_size is not None:
            optics_default["rlnImageSize"] = subtomo_size

        return pd.DataFrame(optics_default)

    def create_optics_group_v4(self, pixel_size=None, subtomo_size=None, binning=None):
        """Creates an optics group with default parameters corresponding to Relion v. 4.x.

        Parameters
        ----------
        pixel_size : float, optional
            The pixel size of the data. If not provided, the pixel size of the object instance (`self.pixel_size`)
            will be used. Defaults to None.
        subtomo_size : int, optional
            The size of the subtomograms. If not provided, it will be set to "NaN". Defaults to None.
        binning : int, optional
            The binning of the subtomograms. If not provided, the binning of the object instance (`self.binning`) will
            be used. Defaults to None.

        Returns
        -------
        pandas.DataFrame
            A DataFrame containing the default optics parameters.

        """
        pixel_size = pixel_size if pixel_size is not None else self.pixel_size
        binning = binning if binning is not None else self.binning
        subtomo_size = subtomo_size if subtomo_size is not None else "NaN"

        unbinned_pixel_size = pixel_size / binning

        optics_default = {
            "rlnOpticsGroup": 1,
            "rlnOpticsGroupName": "opticsGroup1",
            "rlnSphericalAberration": 2.7,
            "rlnVoltage": 300.0,
            "rlnTomoTiltSeriesPixelSize": unbinned_pixel_size,
            "rlnCtfDataAreCtfPremultiplied": 1,
            "rlnImageDimensionality": 3,
            "rlnTomoSubtomogramBinning": binning,
            "rlnImagePixelSize": pixel_size,
            "rlnImageSize": subtomo_size,
        }

        return pd.DataFrame(optics_default)

    def create_final_output(self, relion_df, optics_df=None):
        """Creates the final output frames and specifiers based on the given input dataframes.

        Parameters
        ----------
        relion_df : pandas.DataFrame
            The dataframe containing the relion data.
        optics_df : pandas.DataFrame, optional
            The dataframe containing the optics data. Defaults to None.

        Returns
        -------
        frames : list
            List of pandas.DataFrame containing all data (e.g. particle list, optics group)
        spefifiers : list
            List of `str` containing the specifiers, i.e., the descriptions for the frames.


        Notes
        -----
        If optics_df is None, the frames and specifiers will be based on relion_df and self.data_spec.

        If optics_df is not None, the frames and specifiers will be based on optics_df, relion_df, "data_optics", and
        self.data_spec.

        If self.version is less than 3.1, the frames and specifiers will be based on the concatenated dataframe of
        optics_df and relion_df (with duplicates removed) and self.data_spec.

        """

        if optics_df is None:
            frames = [relion_df]
            specifiers = [self.data_spec]
        else:
            if self.version >= 3.1:
                frames = [optics_df, relion_df]
                specifiers = ["data_optics", self.data_spec]
            else:
                frames = [pd.concat([optics_df, relion_df]).drop_duplicates().reset_index(drop=True)]
                specifiers = [self.data_spec]

        return frames, specifiers

    def create_relion_df(
        self,
        tomo_format="",
        subtomo_format="",
        use_original_entries=False,
        version=None,
        add_object_id=False,
        add_subunit_id=False,
        binning=None,
        pixel_size=None,
        adapt_object_attr=False,
    ):
        """This function creates takes the `self.df` attribute and creates a DataFrame that is Relion format.

        Parameters
        ----------
        tomo_format : str, default=""
            Format of the tomo name output format. See
            :meth:`cryocat.cryomotl.RelionMotl.prepare_particles_data` for more information. Defaults to empty string.
        subtomo_format : str, default=""
            Format of the subtomogram name output format. See
            :meth:`cryocat.cryomotl.RelionMotl.prepare_particles_data` for more information. Defaults to empty string.
        use_original_entries : bool, default=False
            Determine whether to use (True) the original entries stored in `self.relion_df` or not (False). Defaults to False.
        version : float, optional
            Specify the version and thereby the format of the DataFrame. If not provided the value from `self.version`
            will be used. Defaults to None.
        add_object_id : bool, default=False
            Whether to add "object_id" from `self.df` to the DataFrame. If True, the column will be named
            "ccObjectName". Defaults to False.
        add_subunit_id : bool, default=False
            Whether to add "subunit_id" from `self.df` to the DataFrame. If True, the column will be named
            "ccSubunitName". Defaults to False.
        binning : int, optional
            Binning that should be used for conversion in case of Relion v. 4.x. If not provided the value from
            `self.binning` will be used. Defaults to None.
        pixel_size : float, optional
            The pixel size of the data. If not provided, the pixel size of the object instance (`self.pixel_size`)
            will be used. Defaults to None.
        adapt_object_attr : bool, default=False
            Store the created DataFrame to `self.relion_df` attribute of the object. Defaults to False.

        Returns
        -------
        pandas.DataFrame
            A dataframe in Relion format.

        See Also
        --------
        :meth:`cryocat.cryomotl.RelionMotl.prepare_particles_data`
            Provides more info tomo_format and subtomo_format.
        """

        if version is None:
            version = self.version

        if binning is None:
            binning = self.binning

        if pixel_size is None:
            pixel_size = self.pixel_size

        if use_original_entries:
            relion_df = self.adapt_original_entries()
        else:
            relion_df = self.prepare_particles_data(
                tomo_format=tomo_format, subtomo_format=subtomo_format, version=version, pixel_size=pixel_size
            )

        # set coordinates, assumes that subtomograms will be extracted before at exact coordinate with subpixel precision
        relion_df.loc[:, ["rlnCoordinateX", "rlnCoordinateY", "rlnCoordinateZ"]] = self.get_coordinates()

        relion_df = self.convert_angles_to_relion(relion_df)

        relion_df["rlnClassNumber"] = self.df["class"]

        if add_object_id:
            relion_df["ccObjectName"] = self.df["object_id"]

        if add_subunit_id:
            relion_df["ccSubunitName"] = self.df["geom2"]

        if binning != 1.0 and version >= 4.0:
            for coord in ("X", "Y", "Z"):
                relion_df["rlnCoordinate" + coord] = relion_df["rlnCoordinate" + coord] * binning

        relion_df.loc[self.df["subtomo_id"].mod(2).eq(0), "rlnRandomSubset"] = 2
        relion_df.loc[self.df["subtomo_id"].mod(2).eq(1), "rlnRandomSubset"] = 1

        if adapt_object_attr:
            self.relion_df = relion_df

        return relion_df

    def write_out(
        self,
        output_path,
        write_optics=True,
        tomo_format="",
        subtomo_format="",
        use_original_entries=False,
        version=None,
        add_object_id=False,
        add_subunit_id=False,
        binning=None,
        pixel_size=None,
        optics_data=None,
    ):
        """This function converts `self.df` DataFrame to a DataFrame in Relion format and writes it out as a starfile.

        Parameters
        ----------
        ouput_path : str
            The output path to the starfile to be written out.
        write_optics : bool, default=True
            Whether to include optics data in the starfile or not. Defaults to True.
        tomo_format : str, default=""
            Format of the tomo name output format. See
            :meth:`cryocat.cryomotl.RelionMotl.prepare_particles_data` for more information. Defaults to empty string.
        subtomo_format : str, default=""
            Format of the subtomogram name output format. See
            :meth:`cryocat.cryomotl.RelionMotl.prepare_particles_data` for more information. Defaults to empty string.
        use_original_entries : bool, default=False
            Determine whether to use (True) the original entries stored in `self.relion_df`
            or not (False). Defaults to False.
        version : float, optional
            Specify the version and thereby the format of the DataFrame. If not provided the
            value from `self.version` will be used. Defaults to None.
        add_object_id : bool, default=False
            Whether to add "object_id" from `self.df` to the DataFrame. If True,
            the column will be named "ccObjectName". Defaults to False.
        add_subunit_id : bool, default=False
            Whether to add "subunit_id" from `self.df` to the DataFrame. If True,
            the column will be named "ccSubunitName". Defaults to False.
        binning : int, optional
            Binning that should be used for conversion in case of Relion v. 4.x. If not provided the
            value from `self.binning` will be used. Defaults to None.
        pixel_size : float, optional
            The pixel size of the data. If not provided, the pixel size of the object instance (`self.pixel_size`)
            will be used. Defaults to None.
        optics_data : str, optional
            A DataFrame containing optics data or a path to the starfile
            that should be used to fetch the optics from. See :meth:`cryocat.cryomotl.RelionMotl.prepare_optics_data`
            for more details. Used only if `write_optics` is True. If it is None and `write_optics` is True, then
            the attribute `self.optics_df` will be used. Defaults to None.

        Returns
        -------
        None

        See Also
        --------
        :meth:`cryocat.cryomotl.RelionMotl.prepare_particles_data`
            Provides more information tomo_format and subtomo_format.
        :meth:`cryocat.cryomotl.RelionMotl.prepare_optics_data`
            Provide more information on optics_data inputs.

        """
        relion_df = self.create_relion_df(
            use_original_entries=use_original_entries,
            version=version,
            add_object_id=add_object_id,
            add_subunit_id=add_subunit_id,
            tomo_format=tomo_format,
            subtomo_format=subtomo_format,
            binning=binning,
            pixel_size=pixel_size,
            adapt_object_attr=False,
        )

        if write_optics:
            optics_df = self.prepare_optics_data(use_original_entries, optics_data, version)
        else:
            optics_df = None

        frames, specifiers = self.create_final_output(relion_df, optics_df)

        starfileio.Starfile.write(frames, output_path, specifiers=specifiers)


class StopgapMotl(Motl):
    pairs = {
        "subtomo_id": "subtomo_num",
        "tomo_id": "tomo_num",
        "object_id": "object",
        "x": "orig_x",
        "y": "orig_y",
        "z": "orig_z",
        "score": "score",
        "shift_x": "x_shift",
        "shift_y": "y_shift",
        "shift_z": "z_shift",
        "phi": "phi",
        "psi": "psi",
        "theta": "the",
        "class": "class",
    }

    columns = [
        "motl_idx",
        "tomo_num",
        "object",
        "subtomo_num",
        "halfset",
        "orig_x",
        "orig_y",
        "orig_z",
        "score",
        "x_shift",
        "y_shift",
        "z_shift",
        "phi",
        "psi",
        "the",
        "class",
    ]

    def __init__(self, input_motl=None):
        super().__init__()
        self.sg_df = pd.DataFrame()

        if input_motl is not None:
            if isinstance(input_motl, pd.DataFrame):
                self.check_df_type(input_motl)
            elif isinstance(input_motl, str):
                sg_df = self.read_in(input_motl)
                self.convert_to_motl(sg_df)
            else:
                raise UserInputError(
                    f"Provided input_motl is neither DataFrame nor path to the motl file: {input_motl}."
                )

    @staticmethod
    def read_in(input_path):
        """Reads in a starfile in stopgap format and returns the particles as a dataframe in stopgap format.

        Parameters
        ----------
        input_path : str
            The path to the starfile in stopgap format.

        Returns
        -------
        pandas.DataFrame
            The dataframe in the stopgap format containing the particles.

        Raises
        ------
        UserInputError
            If the starfile does not exist.
        UserInputError
            If the starfile does not contain the 'data_stopgap_motivelist' specifier, i.e., is not a particle list.

        """

        frames, specifiers, _ = starfileio.Starfile.read(input_path)

        if "data_stopgap_motivelist" not in specifiers:
            raise UserInputError(f"Provided starfile does not contain particle list: {input_path}.")
        else:
            sg_id = starfileio.Starfile.get_specifier_id("data_stopgap_motivelist")
            stopgap_df = frames[sg_id]

        return stopgap_df

    def convert_to_motl(self, stopgap_df):
        """Converts a stopgap DataFrame to a motl DataFrame and stores it in self.df.

        Parameters
        ----------
        stopgap_df : pandas.DataFrame
            The Stopgap DataFrame to be converted.


        Warnings
        --------
        If the particles are split into A and B halfsets the subtomo_id will be assigned based on them and
        will not correspond to the "subtomo_num" anymore. The "subtomo_num" information will be store in "geom3"
        column instead. New extraction of subtomograms will be neceesary in such a case.

        Notes
        -----
        This method modifies the `df` attribute of the object.

        Returns
        -------
        None

        """

        self.sg_df = stopgap_df

        for em_key, star_key in StopgapMotl.pairs.items():
            self.df[em_key] = stopgap_df[star_key]

        if stopgap_df["halfset"].nunique() == 2:
            self.df["geom3"] = [1.0 if hs.lower() == "a" else 0.0 for hs in stopgap_df["halfset"]]
            halfset_num = self.df["geom3"].values % 2
            subtomo_id_num = []
            c = 0 if halfset_num[0] == 1 else 2
            for i in range(0, self.df.shape[0]):
                c = np.ceil(c / 2) * 2 + halfset_num[i]
                subtomo_id_num.append(c)

            self.df["geom3"] = self.df["subtomo_id"]
            self.df["subtomo_id"] = subtomo_id_num

    @staticmethod
    def convert_to_sg_motl(motl_df, reset_index=False):
        """Converts a given motl DataFrame to a Stopgap DataFrame.

        Parameters
        ----------
        motl_df : pandas.DataFrame
            The input DataFrame in motl format.
        reset_index : bool, default=False
            Whether to reset the index of the resulting DataFrame. Defaults to False.

        Returns
        -------
        pandas.DataFrame
            The converted Stopgap DataFrame.

        """

        stopgap_df = pd.DataFrame(data=np.zeros((motl_df.shape[0], 16)), columns=StopgapMotl.columns)

        for em_key, star_key in StopgapMotl.pairs.items():
            stopgap_df[star_key] = motl_df[em_key]

        stopgap_df.loc[motl_df["subtomo_id"].mod(2).eq(0), "halfset"] = "A"
        stopgap_df.loc[motl_df["subtomo_id"].mod(2).eq(1), "halfset"] = "B"
        stopgap_df["motl_idx"] = stopgap_df["subtomo_num"]

        stopgap_df = StopgapMotl.sg_df_reset_index(stopgap_df, reset_index)

        return stopgap_df

    @staticmethod
    def sg_df_reset_index(stopgap_df, reset_index=False):
        """Resets the "motl_idx" of a stopgap DataFrame to sequence from 1 to the length of the particle list if
        reset_index is True.

        Parameters
        ----------
        stopgap_df : pandas.DataFrame
            The DataFrame to set the "motl_idx" of.
        reset_index : bool, default=False
            Whether to set the "motl_idx" to a sequence from 1 to the length of the particle list or leave the
            original values. Defaults to False.

        Returns
        -------
        pandas.DataFrame
            The DataFrame with the "motl_idx" either reset to the sequence from 1 to the length of the particle
            list or original values.

        """

        if reset_index:
            stopgap_df["motl_idx"] = range(1, stopgap_df.shape[0] + 1)

        return stopgap_df

    def write_out(self, output_path, update_coord=False, reset_index=False):
        """Writes the StopgapMotl object to a star file.

        Parameters
        ----------
        output_path : str
            The path to save the star file.
        update_coord : bool, default=False
            Whether to update the coordinates before writing. Defaults to False.
        reset_index : bool, default=False
            Whether to reset the index of the dataframe before writing. Defaults to False.

        Returns
        -------
        None

        See Also
        --------
        :meth:`cryocat.cryomotl.StopgapMotl.sg_df_reset_index`
            Provides more details on index reseting.

        Examples
        --------
        >>> obj = StopgapMotl()
        >>> obj.write("output.star", update_coord=True, reset_index=True)

        """

        if update_coord:
            self.update_coordinates()

        stopgap_df = StopgapMotl.convert_to_sg_motl(self.df, reset_index)

        starfileio.Starfile.write([stopgap_df], output_path, specifiers=["data_stopgap_motivelist"])


class DynamoMotl(Motl):
    def __init__(self, input_motl=None):
        super().__init__()
        self.dynamo_df = pd.DataFrame()

        if input_motl is not None:
            if isinstance(input_motl, pd.DataFrame):
                self.convert_to_motl(input_motl)
            elif isinstance(input_motl, str):
                sg_df = self.read_in(input_motl)
                self.convert_to_motl(sg_df)
            else:
                raise UserInputError(
                    f"Provided input_motl is neither DataFrame nor path to the motl file: {input_motl}."
                )

    @staticmethod
    def read_in(input_path):
        """Reads in a file from the specified input path and returns a pandas DataFrame in dynamo format.

        Parameters
        ----------
        input_path : str
            The path to the input file.

        Returns
        -------
        pandas.DataFrame
            The DataFrame containing the data read from the file in dynamo format.

        Raises
        ------
        ValueError
            If the provided file does not exist.

        Notes
        -----
        TODO: Test proper functionality.

        """

        if os.path.isfile(input_path):
            dynamo_df = pd.read_table(input_path, sep=" ", header=None)
        else:
            raise ValueError(f"Provided file does not exist: {input_path}.")

        return dynamo_df

    def convert_to_motl(self, dynamo_df):
        """Converts a dynamo DataFrame to a motl DataFrame format.

        Parameters
        ----------
        dynamo_df : pandas.DataFrame
            The dynamo DataFrame to be converted.

        Notes
        -----
        This method modifies the `df` attribute of the object.

        Returns
        -------
        None

        """

        self.dynamo_df = dynamo_df
        self.df["score"] = dynamo_df.loc[:, 9]

        self.df["subtomo_id"] = dynamo_df.loc[:, 0]
        self.df["tomo_id"] = dynamo_df.loc[:, 19]
        self.df["object_id"] = dynamo_df.loc[:, 20]

        self.df["x"] = dynamo_df.loc[:, 23]
        self.df["y"] = dynamo_df.loc[:, 24]
        self.df["z"] = dynamo_df.loc[:, 25]

        self.df["shift_x"] = dynamo_df.loc[:, 3]
        self.df["shift_y"] = dynamo_df.loc[:, 4]
        self.df["shift_z"] = dynamo_df.loc[:, 5]

        self.df["phi"] = -dynamo_df.loc[:, 8]
        self.df["psi"] = -dynamo_df.loc[:, 6]
        self.df["theta"] = -dynamo_df.loc[:, 7]

        self.df["class"] = dynamo_df.loc[:, 21]

    def write_out(self, ouptut_path):
        pass


class ModMotl(Motl):
    def __init__(self, input_path):
        super().__init__()
        if input_path is not None:
            self.df = self.read_in(input_path)

    @staticmethod
    def read_in(input_path):
        # TODO finish - read from folder
        if os.path.isfile(input_path):
            motl_df = pd.read_table(input_path, sep=" ", header=None)


def emmotl2relion(
    input_motl,
    output_motl_path=None,
    tomo_format="",
    subtomo_format="",
    relion_version=3.1,
    pixel_size=1.0,
    binning=1.0,
    flip_handedness=False,
    tomo_dim=None,
    write_optics=False,
    optics_data=None,
    add_object_id=False,
    add_subunit_id=False,
):
    em_motl = EmMotl(input_motl)
    em_motl.update_coordinates()

    if flip_handedness:
        em_motl.flip_handedness(tomo_dimensions=tomo_dim)

    rln_motl = RelionMotl(
        em_motl.df, version=relion_version, pixel_size=pixel_size, binning=binning, optics_data=optics_data
    )

    if output_motl_path is not None:
        rln_motl.write_out(
            output_motl_path,
            write_optics=write_optics,
            add_object_id=add_object_id,
            add_subunit_id=add_subunit_id,
            tomo_format=tomo_format,
            subtomo_format=subtomo_format,
            optics_data=optics_data,
        )

    return rln_motl


def relion2emmotl(
    input_motl,
    output_motl_path=None,
    relion_version=None,
    pixel_size=None,
    binning=None,
    update_coordinates=False,
    flip_handedness=False,
    tomo_dim=None,
):
    rln_motl = RelionMotl(input_motl, version=relion_version, pixel_size=pixel_size, binning=binning)

    if flip_handedness:
        rln_motl.flip_handedness(tomo_dimensions=tomo_dim)

    em_motl = EmMotl(rln_motl.df)

    if update_coordinates:
        em_motl.update_coordinates()

    if output_motl_path is not None:
        em_motl.write_out(output_motl_path)

    return em_motl


def stopgap2emmotl(input_motl, output_motl_path=None, update_coordinates=False):
    sg_motl = StopgapMotl(input_motl)
    em_motl = EmMotl(sg_motl.df)

    if update_coordinates:
        em_motl.update_coordinates()

    if output_motl_path is not None:
        em_motl.write_out(output_motl_path)

    return em_motl


def emmotl2stopgap(input_motl, output_motl_path=None, update_coordinates=False, reset_index=False):
    motl = EmMotl(input_motl)
    sg_motl = StopgapMotl(motl.df)

    if update_coordinates:
        sg_motl.update_coordinates()

    if output_motl_path is not None:
        sg_motl.write_out(output_motl_path, update_coord=False, reset_index=reset_index)

    return sg_motl


def relion2stopgap(input_motl, output_motl_path=None, update_coordinates=False, reset_index=False):
    motl = RelionMotl(input_motl)
    sg_motl = StopgapMotl(motl.df)

    if update_coordinates:
        sg_motl.update_coordinates()

    if output_motl_path is not None:
        sg_motl.write_out(output_motl_path, update_coord=False, reset_index=reset_index)

    return sg_motl


def stopgap2relion(
    input_motl,
    output_motl_path=None,
    tomo_format="",
    subtomo_format="",
    relion_version=3.1,
    pixel_size=1.0,
    binning=1.0,
    flip_handedness=False,
    tomo_dim=None,
    write_optics=False,
    optics_data=None,
    add_object_id=False,
    add_subunit_id=False,
):
    sg_motl = StopgapMotl(input_motl)
    sg_motl.update_coordinates()

    if flip_handedness:
        sg_motl.flip_handedness(tomo_dimensions=tomo_dim)

    rln_motl = RelionMotl(
        sg_motl.df, version=relion_version, pixel_size=pixel_size, binning=binning, optics_data=optics_data
    )

    if output_motl_path is not None:
        rln_motl.write_out(
            output_motl_path,
            write_optics=write_optics,
            add_object_id=add_object_id,
            add_subunit_id=add_subunit_id,
            tomo_format=tomo_format,
            subtomo_format=subtomo_format,
            optics_data=optics_data,
        )

    return rln_motl
